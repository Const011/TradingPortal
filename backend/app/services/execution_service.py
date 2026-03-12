"""Execution service: submits entry orders, owns current.json/index.jsonl updates from exchange state."""

import logging
import time
from dataclasses import dataclass

from app.config import settings
from app.services.bybit_client import BybitClient
from app.services.execution_types import ExecutorEntryResponse
from app.services.trade_log import (
    add_current_trade,
    append_entry_index_line,
    append_exit,
    append_stop_move,
    load_current_trades,
    remove_current_trade,
    update_current_trade_stop,
)
from app.services.trading_strategy.types import TradeEvent

logger = logging.getLogger(__name__)


@dataclass
class _PendingEntry:
    trade_id: str
    order_id: str
    order_link_id: str | None
    side: str
    initial_stop_price: float
    target_price: float | None
    requested_entry_price: float  # strategy's intended entry price (ev.price), for entry efficiency
    bar_index: int


# In-memory pending entry per (symbol, interval). One open order at a time per symbol/interval.
_pending_by_key: dict[tuple[str, str], _PendingEntry] = {}


def _bybit_side(side: str | None) -> str:
    if side == "long":
        return "Buy"
    if side == "short":
        return "Sell"
    return "Buy"


async def _get_live_position_state(
    client: BybitClient,
    symbol: str,
) -> tuple[float, str | None]:
    """Return (position_size, side) for the configured market.

    - Linear: use get_linear_positions (size/side from positions API).
    - Spot: approximate "position" from unified wallet base-coin balance; side is always 'long' if size > 0.
    """
    if settings.market == "linear":
        positions = await client.get_linear_positions(symbol=symbol)
        size = _linear_position_size(positions)
        side = _linear_position_side(positions) if size > 0 else None
        return size, side

    # Spot: derive net base-asset size from wallet balance for the base coin.
    # Example: BTCUSDT -> base_coin="BTC".
    base_coin = symbol[:-4] if symbol.endswith("USDT") else symbol[:-3]
    try:
        result = await client.get_wallet_balance(accountType="UNIFIED", coin=base_coin)
    except Exception as e:
        logger.warning(
            "Executor: get_wallet_balance failed when checking spot position symbol=%s base=%s err=%s",
            symbol,
            base_coin,
            e,
        )
        return 0.0, None

    size = 0.0
    for acct in result.get("list", []):
        for coin in acct.get("coin", []):
            if coin.get("coin") != base_coin:
                continue
            try:
                size = float(coin.get("walletBalance", "0") or 0)
            except (TypeError, ValueError):
                size = 0.0
    side: str | None = "long" if size > 0 else None
    return size, side


async def submit_entry(
    ev: TradeEvent,
    symbol: str,
    interval: str,
    client: BybitClient,
) -> ExecutorEntryResponse:
    """Place entry order (market + 0.01% slippage + stopLoss in one request). Linear: set leverage first.
    Registers order as pending; does not write current.json or index.jsonl. Returns order_received, entry_yet=False."""
    trade_id = str(ev.time)
    key = (symbol.upper(), interval)
    if key in _pending_by_key:
        return ExecutorEntryResponse(
            order_received=True,
            entry_yet=False,
            message="Pending entry already exists for this symbol/interval",
        )

    qty = (settings.position_size or "").strip()
    if not qty:
        return ExecutorEntryResponse(
            order_received=False,
            entry_yet=False,
            message="POSITION_SIZE not set",
        )

    new_side = ev.side or "long"

    # Reversal: if we have an open position in the opposite direction, close it first (cancel orders, close, then enter).
    if settings.executor_dry_run:
        current = load_current_trades(symbol, interval)
        has_position = len(current) > 0
        current_side = current[0].get("side", "long") if current else None
    else:
        position_size, current_side = await _get_live_position_state(client, symbol)
        has_position = position_size > 0

    if has_position and current_side and current_side != new_side:
        logger.info(
            "Executor: reversal detected (current=%s new=%s), closing position then entering symbol=%s",
            current_side,
            new_side,
            symbol,
        )
        if not settings.executor_dry_run and client:
            try:
                category = settings.market
                open_orders = await client.get_open_orders(category=category, symbol=symbol)
                for order in open_orders:
                    order_id = order.get("orderId")
                    if order_id:
                        await client.cancel_order(category=category, symbol=symbol, orderId=order_id)
                        logger.info("Executor: cancelled open order orderId=%s (reversal) symbol=%s", order_id, symbol)
            except Exception as e:
                logger.warning("Executor: cancel open orders before reversal failed symbol=%s err=%s", symbol, e)
        close_result = await close_position(symbol, interval, client)
        if not close_result.get("ok"):
            return ExecutorEntryResponse(
                order_received=False,
                entry_yet=False,
                message=f"Reversal close failed: {close_result.get('message', 'unknown')}",
            )
        logger.info("Executor: reversal close done, placing new entry side=%s", new_side)

    category = settings.market
    side = _bybit_side(new_side)

    try:
        # ----- TEMPORARY DEBUG STUBS: dry run = no real Bybit calls, treat as success and update log/current -----
        if settings.executor_dry_run:
            params = (
                f"symbol={symbol} side={side} qty={qty} orderType=Market slippageToleranceType=Percent "
                f"slippageTolerance=0.01 stopLoss={ev.initial_stop_price} category={category} "
                f"trade_id={trade_id} leverage={settings.leverage}"
            )
            print(f" [TEMPORARY DEBUG STUB] position entry with params: {params}")
            entry_time = int(ev.time)
            size_float = float(qty) if qty else 0.0
            add_current_trade(
                symbol=symbol,
                interval=interval,
                trade_id=trade_id,
                entry_time=entry_time,
                entry_price=ev.price,
                initial_stop_price=ev.initial_stop_price,
                side=ev.side or "long",
                target_price=ev.target_price,
                size=size_float,
            )
            append_entry_index_line(
                symbol=symbol,
                interval=interval,
                trade_id=trade_id,
                entry_time=entry_time,
                entry_price=ev.price,
                side=ev.side or "long",
                initial_stop_price=ev.initial_stop_price,
                target_price=ev.target_price,
                requested_entry_price=ev.price,
                size=size_float,
                bar_index=ev.bar_index,
            )
            logger.info("Executor: [DRY RUN] entry simulated trade_id=%s", trade_id)
            return ExecutorEntryResponse(order_received=True, entry_yet=False, message="dry run: simulated fill")
        # ----- end TEMPORARY DEBUG STUBS -----

        # ----- LIVE (runs when EXECUTOR_DRY_RUN=false) -----
        if category == "linear":
            logger.info(
                "Executor: calling set_linear_leverage symbol=%s leverage=%s",
                symbol,
                settings.leverage,
            )
            try:
                await client.set_linear_leverage(
                    symbol=symbol,
                    buyLeverage=settings.leverage,
                    sellLeverage=settings.leverage,
                )
            except BybitClientError as e:
                # Bybit returns "leverage not modified" when the requested leverage
                # matches the existing value. Treat this as a non-fatal no-op and
                # continue to place the entry order.
                if (e.ret_msg or "").lower().startswith("leverage not modified"):
                    logger.info(
                        "Executor: set_linear_leverage no-op (unchanged) symbol=%s leverage=%s",
                        symbol,
                        settings.leverage,
                    )
                else:
                    raise
        logger.info(
            "Executor: calling create_order category=%s symbol=%s side=%s qty=%s",
            category,
            symbol,
            side,
            qty,
        )
        result = await client.create_order(
            category=category,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty,
            slippageToleranceType="Percent",
            slippageTolerance="0.01",
            stopLoss=str(ev.initial_stop_price),
        )
        order_id = (result or {}).get("orderId", "")
        order_link_id = (result or {}).get("orderLinkId")
        _pending_by_key[key] = _PendingEntry(
            trade_id=trade_id,
            order_id=order_id,
            order_link_id=order_link_id,
            side=ev.side or "long",
            initial_stop_price=ev.initial_stop_price,
            target_price=ev.target_price,
            requested_entry_price=ev.price,
            bar_index=ev.bar_index,
        )
        logger.info(
            "Executor: entry order submitted trade_id=%s orderId=%s symbol=%s",
            trade_id,
            order_id,
            symbol,
        )
        return ExecutorEntryResponse(
            order_received=True,
            entry_yet=False,
            order_id=order_id,
            order_link_id=order_link_id,
        )
    except Exception as e:
        logger.exception("Executor: submit_entry failed trade_id=%s", trade_id)
        return ExecutorEntryResponse(
            order_received=False,
            entry_yet=False,
            message=str(e),
        )


def _linear_position_size(positions: list[dict]) -> float:
    """Sum of position sizes for symbol (same symbol in list). Bybit returns one row per side."""
    total = 0.0
    for pos in positions:
        try:
            total += float(pos.get("size", "0") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _linear_position_side(positions: list[dict]) -> str | None:
    """Return 'long' or 'short' for the open position, or None if no size. Bybit returns one row per side."""
    for pos in positions:
        try:
            size = float(pos.get("size", "0") or 0)
            if size > 0:
                return "long" if pos.get("side") == "Buy" else "short"
        except (TypeError, ValueError):
            pass
    return None


def _fake_positions_from_current(
    symbol: str,
    interval: str,
    current_low: float | None = None,
    current_high: float | None = None,
) -> list[dict]:
    """TEMPORARY DEBUG STUB: build Bybit-like position list from current.json for dry run.

    When current_low/current_high are provided, this simulates stop-hit by
    omitting positions whose effective stop would have been touched on the
    current bar (long: bar low <= stop; short: bar high >= stop). This causes
    sync_from_exchange to see position_size == 0 while current.json still has
    open trades, which in turn triggers the normal stop-hit exit path.
    """
    current = load_current_trades(symbol, interval)
    print(f'[DRY_RUN] current: {current}, current_low: {current_low}, current_high: {current_high}')
    out: list[dict] = []
    for t in current:
        side = t.get("side", "long")
        stop_price = float(
            t.get("currentStopPrice")
            or t.get("initialStopPrice")
            or t.get("entryPrice")
            or 0.0
        )
        logger.info(
            "Executor: [DRY RUN] stop-sim trade_id=%s side=%s stop=%.4f low=%s high=%s",
            t.get("tradeId"),
            side,
            stop_price,
            f"{current_low:.4f}" if current_low is not None else "None",
            f"{current_high:.4f}" if current_high is not None else "None",
        )
        # If we have current bar prices, simulate that the exchange closed
        # the position when price touched the stop.
        if current_low is not None and current_high is not None:
            if side == "long" and current_low <= stop_price:
                logger.info(
                    "Executor: [DRY RUN] stop-sim HIT (long) trade_id=%s stop=%.4f low=%.4f",
                    t.get("tradeId"),
                    stop_price,
                    current_low,
                )
                # Stop hit for long: no live position returned for this trade.
                continue
            if side == "short" and current_high >= stop_price:
                logger.info(
                    "Executor: [DRY RUN] stop-sim HIT (short) trade_id=%s stop=%.4f high=%.4f",
                    t.get("tradeId"),
                    stop_price,
                    current_high,
                )
                # Stop hit for short: no live position returned for this trade.
                continue
        bybit_side = "Buy" if side == "long" else "Sell"
        size = t.get("size", settings.position_size or "0")
        if size is None:
            size = settings.position_size or "0"
        size_str = str(size)
        avg = str(t.get("entryPrice", "0"))
        out.append({"symbol": symbol, "side": bybit_side, "size": size_str, "avgPrice": avg})
    return out


async def sync_from_exchange(
    symbol: str,
    interval: str,
    client: BybitClient,
) -> list[str]:
    """1) If pending entry filled: write current.json and index.jsonl, clear pending.
    2) If position size 0 but current.json has open trade(s): stop hit → append exit, remove from current.json.
    Returns list of trade_ids that were closed by stop-hit (so caller can add to logged_exit_ids)."""
    exited_ids: list[str] = []
    key = (symbol.upper(), interval)

    try:
        if settings.executor_dry_run:
            current_low: float | None = None
            current_high: float | None = None
            try:
                # Fetch the latest bar to simulate stop-hit based on bar range
                candles = await client.get_klines(symbol=symbol, interval=interval, limit=1)
                if candles:
                    last_candle = candles[-1]
                    current_low = float(getattr(last_candle, "low", 0.0) or 0.0)
                    current_high = float(getattr(last_candle, "high", 0.0) or 0.0)
            except Exception as e:
                logger.warning(
                    "Executor: failed to fetch candles for dry-run stop simulation symbol=%s interval=%s err=%s",
                    symbol,
                    interval,
                    e,
                )
            positions = _fake_positions_from_current(symbol, interval, current_low, current_high)
        else:
            # Live mode: derive position size from exchange state based on market.
            logger.info("Executor: sync_from_exchange fetching live position state symbol=%s market=%s", symbol, settings.market)
            position_size, _ = await _get_live_position_state(client, symbol)
            positions: list[dict] = []
        # ----- end TEMPORARY DEBUG STUBS -----

        # 1) Pending entry: try to confirm fill (skip in dry run; we simulate immediate fill in submit_entry)
        pending = _pending_by_key.get(key)
        if pending and not settings.executor_dry_run and settings.market == "linear":
            # For now, only linear uses exchange positions to confirm entry fill.
            category = settings.market
            open_orders = await client.get_open_orders(category=category, symbol=symbol)
            order_ids = {o.get("orderId") for o in open_orders}
            if pending.order_id not in order_ids and position_size > 0:
                positions = await client.get_linear_positions(symbol=symbol)
                side_key = "Buy" if pending.side == "long" else "Sell"
                for pos in positions:
                    if pos.get("side") != side_key:
                        continue
                    size_str = pos.get("size", "0")
                    avg_price_str = pos.get("avgPrice", "0")
                    try:
                        size = float(size_str)
                        entry_price = float(avg_price_str)
                    except (TypeError, ValueError):
                        continue
                    if size <= 0:
                        continue
                    entry_time = int(pending.trade_id) if pending.trade_id.isdigit() else 0
                    add_current_trade(
                        symbol=symbol,
                        interval=interval,
                        trade_id=pending.trade_id,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        initial_stop_price=pending.initial_stop_price,
                        side=pending.side,
                        target_price=pending.target_price,
                        size=size,
                    )
                    append_entry_index_line(
                        symbol=symbol,
                        interval=interval,
                        trade_id=pending.trade_id,
                        entry_time=entry_time,
                        entry_price=entry_price,
                        side=pending.side,
                        initial_stop_price=pending.initial_stop_price,
                        target_price=pending.target_price,
                        requested_entry_price=pending.requested_entry_price,
                        size=size,
                        bar_index=pending.bar_index,
                    )
                    logger.info(
                        "Executor: confirmed entry trade_id=%s entry_price=%s size=%s",
                        pending.trade_id,
                        entry_price,
                        size,
                    )
                    del _pending_by_key[key]
                    return exited_ids

        # 2) Stop-hit: position size 0 but current.json has trades → cancel any open orders for symbol, append exit, remove from current.json
        current = load_current_trades(symbol, interval)
        logger.info(
            "Executor: stop-hit check position_size=%s current_trades=%s",
            position_size,
            current,
        )
        if position_size <= 0 and len(current) > 0:
            
            logger.info(
                "Executor: stop hit detected (position size 0), registering exit(s) and updating current.json symbol=%s",
                symbol,
            )
            if not settings.executor_dry_run and client:
                try:
                    category = settings.market
                    open_orders = await client.get_open_orders(category=category, symbol=symbol)
                    for order in open_orders:
                        order_id = order.get("orderId")
                        if order_id:
                            await client.cancel_order(category=category, symbol=symbol, orderId=order_id)
                            logger.info("Executor: cancelled open order orderId=%s (symbol=%s)", order_id, symbol)
                except Exception as e:
                    logger.warning("Executor: cancel open orders after stop hit failed symbol=%s err=%s", symbol, e)
            close_time = int(time.time())
            for t in current:
                trade_id = t.get("tradeId", "")
                if not trade_id:
                    continue
                entry_price = float(t.get("entryPrice", 0) or 0)
                stop_price = float(t.get("currentStopPrice", 0) or t.get("initialStopPrice", 0) or entry_price)
                side = t.get("side", "long")
                if side == "long":
                    points = stop_price - entry_price
                else:
                    points = entry_price - stop_price
                append_exit(
                    symbol=symbol,
                    interval=interval,
                    trade_id=trade_id,
                    time=close_time,
                    close_price=stop_price,
                    close_reason="stop",
                    points=points,
                )
                remove_current_trade(symbol, interval, trade_id)
                exited_ids.append(trade_id)
                logger.info(
                    "Executor: stop hit trade_id=%s close_price=%s position closed",
                    trade_id,
                    stop_price,
                )
    except Exception:
        logger.exception("Executor: sync_from_exchange failed symbol=%s", symbol)
    return exited_ids


async def update_stop(
    symbol: str,
    interval: str,
    trade_id: str,
    new_stop_price: float,
    side: str,
    end_time: int,
    client: BybitClient | None = None,
) -> None:
    """Update trailing stop: write current.json + index (stop_move).
    Dry run: log only, no Bybit. Live: set exchange stop when supported, then persist."""
    logger.info(
        "Executor: update_stop called symbol=%s interval=%s trade_id=%s side=%s new_stop=%.4f market=%s dry_run=%s",
        symbol,
        interval,
        trade_id,
        side,
        new_stop_price,
        settings.market,
        settings.executor_dry_run,
    )
    if settings.executor_dry_run:
        logger.info(
            "Executor: [DRY RUN] stop moved trade_id=%s new_stop=%.4f side=%s",
            trade_id,
            new_stop_price,
            side,
        )
        update_current_trade_stop(symbol, interval, trade_id, new_stop_price)
        append_stop_move(symbol, interval, trade_id, end_time, new_stop_price, side)
        return
    if client is None:
        logger.warning("Executor: update_stop skipped (no client)")
        return
    # Live mode: only linear has native trading-stop; spot updates are local-only.
    if settings.market == "linear":
        try:
            logger.info(
                "Executor: calling set_linear_trading_stop symbol=%s trade_id=%s new_stop=%.4f",
                symbol,
                trade_id,
                new_stop_price,
            )
            await client.set_linear_trading_stop(symbol=symbol, stopLoss=new_stop_price)
            logger.info(
                "Executor: set_linear_trading_stop trade_id=%s stopLoss=%s",
                trade_id,
                new_stop_price,
            )
        except Exception as e:
            logger.warning(
                "Executor: set_linear_trading_stop failed trade_id=%s err=%s",
                trade_id,
                e,
            )
            # Even if the exchange call fails, we still update local state so UI/logs reflect intent.
    else:
        logger.info(
            "Executor: update_stop spot-only (no native trailing stop) trade_id=%s new_stop=%.4f",
            trade_id,
            new_stop_price,
        )
    update_current_trade_stop(symbol, interval, trade_id, new_stop_price)
    append_stop_move(symbol, interval, trade_id, end_time, new_stop_price, side)


async def close_position(
    symbol: str,
    interval: str,
    client: BybitClient,
) -> dict[str, str | float | None]:
    """Close live position for current market: get live size, place opposite market order,
    append exit and remove from current.json. Returns result dict with ok, message,
    and optionally close_price/points."""
    try:
        # ----- TEMPORARY DEBUG STUB: read from current.json, no real Bybit close -----
        if settings.executor_dry_run:
            current = load_current_trades(symbol, interval)
            for t in current:
                trade_id = t.get("tradeId", "")
                if not trade_id:
                    continue
                entry_price = float(t.get("entryPrice", 0) or 0)
                stop_price = float(t.get("currentStopPrice", 0) or t.get("initialStopPrice", 0) or entry_price)
                side = t.get("side", "long")
                points = (stop_price - entry_price) if side == "long" else (entry_price - stop_price)
                print(f" [TEMPORARY DEBUG STUB] close position with params: symbol={symbol} trade_id={trade_id} close_price={stop_price} reason=manual")
                append_exit(
                    symbol=symbol,
                    interval=interval,
                    trade_id=trade_id,
                    time=int(time.time()),
                    close_price=stop_price,
                    close_reason="manual",
                    points=points,
                )
                remove_current_trade(symbol, interval, trade_id)
                return {"ok": True, "close_price": stop_price, "points": points, "message": "dry run: simulated close"}
            return {"ok": False, "message": "dry run: no trade in current.json"}
        # ----- end TEMPORARY DEBUG STUBS -----

        # Live close: use market-aware position state and create an opposite market order.
        position_size, position_side = await _get_live_position_state(client, symbol)
        if position_size <= 0 or position_side is None:
            return {"ok": False, "message": "No live position to close"}

        close_side = "Sell" if position_side == "long" else "Buy"
        category = settings.market
        logger.info(
            "Executor: calling close create_order category=%s symbol=%s side=%s qty=%s",
            category,
            symbol,
            close_side,
            position_size,
        )
        await client.create_order(
            category=category,
            symbol=symbol,
            side=close_side,
            orderType="Market",
            qty=str(position_size),
            reduceOnly=True if category == "linear" else None,
        )

        # Reload current.json and write exit against matching trade(s).
        current = load_current_trades(symbol, interval)
        for t in current:
            if t.get("side") != position_side:
                continue
            trade_id = t.get("tradeId", "")
            if not trade_id:
                continue
            entry_price = float(t.get("entryPrice", 0) or 0)
            # We don't know exact fill price of the close order here; approximate with entry +/- 0 for now.
            # Caller can reconcile with exchange fills if needed.
            # For consistency with linear path, treat avg_price ~= entry_price for points calc placeholder.
            avg_price = entry_price
            if position_side == "long":
                points = avg_price - entry_price
            else:
                points = entry_price - avg_price
            append_exit(
                symbol=symbol,
                interval=interval,
                trade_id=trade_id,
                time=int(time.time()),
                close_price=avg_price,
                close_reason="manual",
                points=points,
            )
            remove_current_trade(symbol, interval, trade_id)
            logger.info("Executor: closed position trade_id=%s", trade_id)
            return {"ok": True, "close_price": avg_price, "points": points}
        return {"ok": True, "message": "Position closed, no matching trade in current.json"}
    except Exception as e:
        logger.exception("Executor: close_position failed symbol=%s", symbol)
        return {"ok": False, "message": str(e)}
    return {"ok": False, "message": "No position to close"}
