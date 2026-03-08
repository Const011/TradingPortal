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
    load_current_trades,
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
    bar_index: int


# In-memory pending entry per (symbol, interval). One open order at a time per symbol/interval.
_pending_by_key: dict[tuple[str, str], _PendingEntry] = {}


def _bybit_side(side: str | None) -> str:
    if side == "long":
        return "Buy"
    if side == "short":
        return "Sell"
    return "Buy"


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

    category = settings.market
    side = _bybit_side(ev.side)

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
                size=size_float,
                bar_index=ev.bar_index,
            )
            logger.info("Executor: [DRY RUN] entry simulated trade_id=%s", trade_id)
            return ExecutorEntryResponse(order_received=True, entry_yet=False, message="dry run: simulated fill")
        # ----- end TEMPORARY DEBUG STUBS -----

        # ----- LIVE (runs when EXECUTOR_DRY_RUN=false) -----
        if category == "linear":
            await client.set_linear_leverage(
                symbol=symbol,
                buyLeverage=settings.leverage,
                sellLeverage=settings.leverage,
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


def _fake_positions_from_current(symbol: str, interval: str) -> list[dict]:
    """TEMPORARY DEBUG STUB: build Bybit-like position list from current.json for dry run."""
    current = load_current_trades(symbol, interval)
    out: list[dict] = []
    for t in current:
        side = t.get("side", "long")
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
    if settings.market != "linear":
        return exited_ids
    key = (symbol.upper(), interval)

    try:
        # ----- TEMPORARY DEBUG STUB: read position from current.json instead of Bybit -----
        if settings.executor_dry_run:
            positions = _fake_positions_from_current(symbol, interval)
        else:
            positions = await client.get_linear_positions(symbol=symbol)
        # ----- end TEMPORARY DEBUG STUBS -----
        position_size = _linear_position_size(positions)

        # 1) Pending entry: try to confirm fill (skip in dry run; we simulate immediate fill in submit_entry)
        pending = _pending_by_key.get(key)
        if pending and not settings.executor_dry_run:
            open_orders = await client.get_open_orders(category="linear", symbol=symbol)
            order_ids = {o.get("orderId") for o in open_orders}
            if pending.order_id not in order_ids and position_size > 0:
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

        # 2) Stop-hit: position size 0 but current.json has trades → append exit, remove from current
        if position_size <= 0:
            current = load_current_trades(symbol, interval)
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
                exited_ids.append(trade_id)
                logger.info(
                    "Executor: stop hit trade_id=%s close_price=%s",
                    trade_id,
                    stop_price,
                )
    except Exception:
        logger.exception("Executor: sync_from_exchange failed symbol=%s", symbol)
    return exited_ids


async def close_position(
    symbol: str,
    interval: str,
    client: BybitClient,
) -> dict[str, str | float | None]:
    """Close linear position: get live size, place opposite market order, append exit and remove from current.json.
    Returns result dict with ok, message, and optionally close_price/points."""
    if settings.market != "linear":
        return {"ok": False, "message": "Only linear supported"}
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
                return {"ok": True, "close_price": stop_price, "points": points, "message": "dry run: simulated close"}
            return {"ok": False, "message": "dry run: no trade in current.json"}
        # ----- end TEMPORARY DEBUG STUBS -----

        positions = await client.get_linear_positions(symbol=symbol)
        for pos in positions:
            size_str = pos.get("size", "0")
            avg_str = pos.get("avgPrice", "0")
            side = pos.get("side", "")
            try:
                size = float(size_str)
                avg_price = float(avg_str)
            except (TypeError, ValueError):
                continue
            if size <= 0:
                continue
            close_side = "Sell" if side == "Buy" else "Buy"
            await client.create_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=size_str,
                reduceOnly=True,
            )
            current = load_current_trades(symbol, interval)
            trade_side = "long" if side == "Buy" else "short"
            for t in current:
                if t.get("side") != trade_side:
                    continue
                trade_id = t.get("tradeId", "")
                if not trade_id:
                    continue
                entry_price = float(t.get("entryPrice", 0) or 0)
                if trade_side == "long":
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
                logger.info("Executor: closed position trade_id=%s", trade_id)
                return {"ok": True, "close_price": avg_price, "points": points}
            return {"ok": True, "message": "Position closed, no matching trade in current.json"}
    except Exception as e:
        logger.exception("Executor: close_position failed symbol=%s", symbol)
        return {"ok": False, "message": str(e)}
    return {"ok": False, "message": "No position to close"}
