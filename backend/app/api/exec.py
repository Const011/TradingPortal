"""Manual execution endpoints: test Bybit order/position/wallet operations via curl. No trade log."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.bybit_client import BybitClient, BybitClientError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exec", tags=["exec"])


def _default_category() -> str:
    return settings.market


def get_bybit_client() -> BybitClient:
    return BybitClient()


# ---------- Request bodies ----------


class OrderCreateBody(BaseModel):
    symbol: str = Field(..., description="e.g. BTCUSDT")
    side: str = Field(..., description="Buy | Sell")
    orderType: str = Field(..., description="Market | Limit")
    qty: str = Field(..., description="Quantity (string); for spot market use marketUnit=quoteCoin and qty=USDT amount")
    price: str | None = Field(None, description="Required for Limit")
    category: str | None = Field(None, description="spot | linear; default from config")
    marketUnit: str | None = Field(None, description="Spot market only: quoteCoin (qty in USDT) | baseCoin (qty in base)")
    # TP/SL on order: applied automatically when the order fills (no separate trading-stop call needed).
    takeProfit: float | None = Field(None, description="Take profit price; linear: use tpslMode/tpOrderType if needed")
    stopLoss: float | None = Field(None, description="Stop loss price")
    tpslMode: str | None = Field(None, description="Linear: Partial | Full (Full = entire position, tpOrderType/slOrderType Market)")
    tpOrderType: str | None = Field(None, description="Linear: Market | Limit when tpslMode=Partial")
    slOrderType: str | None = Field(None, description="Linear: Market | Limit when tpslMode=Partial")


class OrderCancelBody(BaseModel):
    symbol: str = Field(...)
    category: str = Field(..., description="spot | linear")
    orderId: str | None = Field(None)
    orderLinkId: str | None = Field(None)


class TradingStopBody(BaseModel):
    symbol: str = Field(...)
    stopLoss: float | None = Field(None)
    takeProfit: float | None = Field(None)
    trailingStop: str | None = Field(None)
    slTriggerBy: str | None = Field(None)
    tpTriggerBy: str | None = Field(None)


class ClosePositionBody(BaseModel):
    symbol: str = Field(...)
    category: str = Field(..., description="spot | linear")


class SetLeverageBody(BaseModel):
    symbol: str = Field(...)
    buyLeverage: int = Field(..., description="e.g. 10 for 10x")
    sellLeverage: int | None = Field(None, description="defaults to buyLeverage (one-way)")


def _ensure_auth(client: BybitClient) -> None:
    if not client._has_private_auth():
        raise HTTPException(
            status_code=503,
            detail="Bybit API key/secret not configured (BYBIT_API_KEY, BYBIT_API_SECRET)",
        )


def _bybit_error_to_http(e: BybitClientError) -> HTTPException:
    return HTTPException(
        status_code=400 if e.ret_code and e.ret_code != 0 else 502,
        detail={"retCode": e.ret_code, "retMsg": e.ret_msg or str(e)},
    )


@router.post("/order")
async def exec_create_order(
    body: OrderCreateBody,
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Place order (market or limit). Manual mode: no trade log."""
    _ensure_auth(client)
    category = body.category or _default_category()
    order_kwargs: dict[str, str | float | None] = {"marketUnit": body.marketUnit}
    if body.takeProfit is not None:
        order_kwargs["takeProfit"] = str(body.takeProfit)
    if body.stopLoss is not None:
        order_kwargs["stopLoss"] = str(body.stopLoss)
    for k in ("tpslMode", "tpOrderType", "slOrderType"):
        v = getattr(body, k, None)
        if v is not None:
            order_kwargs[k] = v
    try:
        result = await client.create_order(
            category=category,
            symbol=body.symbol,
            side=body.side,
            orderType=body.orderType,
            qty=body.qty,
            price=body.price,
            **order_kwargs,
        )
        return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.post("/order/cancel")
async def exec_cancel_order(
    body: OrderCancelBody,
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Cancel order by orderId or orderLinkId."""
    _ensure_auth(client)
    if not body.orderId and not body.orderLinkId:
        raise HTTPException(status_code=400, detail="Provide orderId or orderLinkId")
    try:
        result = await client.cancel_order(
            category=body.category,
            symbol=body.symbol,
            orderId=body.orderId,
            orderLinkId=body.orderLinkId,
        )
        return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.get("/orders")
async def exec_get_orders(
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    category: str = Query(..., description="spot | linear"),
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """List open orders for symbol."""
    _ensure_auth(client)
    try:
        orders = await client.get_open_orders(category=category, symbol=symbol)
        return {"ok": True, "list": orders}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.get("/wallet-balance")
async def exec_wallet_balance(
    accountType: str = Query("UNIFIED", description="UNIFIED | CONTRACT | SPOT"),
    coin: str | None = Query(None),
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Unified (or other) wallet balance. Optional coin filter."""
    _ensure_auth(client)
    try:
        result = await client.get_wallet_balance(accountType=accountType, coin=coin)
        return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.get("/positions")
async def exec_positions(
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Linear positions for symbol."""
    _ensure_auth(client)
    try:
        positions = await client.get_linear_positions(symbol=symbol)
        return {"ok": True, "list": positions}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.post("/positions/trading-stop")
async def exec_set_trading_stop(
    body: TradingStopBody,
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Set linear position SL/TP/TS. Manual mode: no trade log."""
    _ensure_auth(client)
    try:
        result = await client.set_linear_trading_stop(
            symbol=body.symbol,
            stopLoss=body.stopLoss,
            takeProfit=body.takeProfit,
            trailingStop=body.trailingStop,
            slTriggerBy=body.slTriggerBy,
            tpTriggerBy=body.tpTriggerBy,
        )
        return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.post("/positions/set-leverage")
async def exec_set_leverage(
    body: SetLeverageBody,
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Set linear leverage (e.g. 10x). Call before placing orders."""
    _ensure_auth(client)
    try:
        result = await client.set_linear_leverage(
            symbol=body.symbol,
            buyLeverage=body.buyLeverage,
            sellLeverage=body.sellLeverage,
        )
        return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)


@router.post("/positions/close")
async def exec_close_position(
    body: ClosePositionBody,
    client: BybitClient = Depends(get_bybit_client),
) -> dict[str, Any]:
    """Close position: query live size, place opposite market order. Manual mode: no trade log."""
    _ensure_auth(client)
    try:
        if body.category == "linear":
            positions = await client.get_linear_positions(symbol=body.symbol)
            # Bybit: size is string, always positive; side is "Buy" (long) or "Sell" (short).
            size = 0.0
            pos_side = ""
            for p in positions:
                pos_size = float(p.get("size", 0) or 0)
                if pos_size > 0:
                    size = pos_size
                    pos_side = p.get("side") or "Buy"
                    break
            if size <= 0:
                return {
                    "ok": True,
                    "result": "no_position",
                    "message": "No open linear position",
                }
            close_side = "Sell" if pos_side == "Buy" else "Buy"
            qty_str = str(int(size)) if size == int(size) else str(size)
            result = await client.create_order(
                category="linear",
                symbol=body.symbol,
                side=close_side,
                orderType="Market",
                qty=qty_str,
                reduceOnly=True,
            )
            return {"ok": True, "result": result}
        else:
            # Spot: get base coin balance and sell it. Symbol assumed BASEUSDT -> base BASE.
            base_coin = body.symbol.replace("USDT", "").replace("USDC", "")
            if not base_coin:
                raise HTTPException(status_code=400, detail="Unsupported spot symbol")
            wallet = await client.get_wallet_balance(accountType="UNIFIED")
            # Bybit v5: result.list[] = accounts, each has .coin[] with .coin, .walletBalance, .availableToWithdraw
            accounts = wallet.get("list", [])
            available = 0.0
            for acc in accounts:
                coins = acc.get("coin") or []
                if not isinstance(coins, list):
                    continue
                for c in coins:
                    if c.get("coin") == base_coin:
                        available = float(
                            c.get("availableToWithdraw", 0) or c.get("walletBalance", 0) or 0
                        )
                        break
                if available > 0:
                    break
            if available <= 0:
                return {
                    "ok": True,
                    "result": "no_balance",
                    "message": f"No {base_coin} balance to close",
                }
            qty_str = str(available) if available < 1e8 else str(int(available))
            result = await client.create_order(
                category="spot",
                symbol=body.symbol,
                side="Sell",
                orderType="Market",
                qty=qty_str,
            )
            return {"ok": True, "result": result}
    except BybitClientError as e:
        raise _bybit_error_to_http(e)
