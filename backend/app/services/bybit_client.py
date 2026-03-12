import hmac
import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator
from urllib.parse import urlencode

import httpx
import websockets

from app.config import settings
from app.schemas.market import BarUpdate, Candle, SymbolInfo, TickerSnapshot, TickerTick
from app.utils.timefmt import ts_human


logger = logging.getLogger(__name__)


class BybitClientError(Exception):
    """Raised when Bybit API returns retCode != 0 or HTTP error."""

    def __init__(self, message: str, ret_code: int | None = None, ret_msg: str | None = None):
        super().__init__(message)
        self.ret_code = ret_code
        self.ret_msg = ret_msg


class BybitClient:
    """Client for Bybit REST and WebSocket APIs. All methods communicate with Bybit."""

    def _market_category(self) -> str:
        """Return Bybit category based on app-level market setting."""
        return "spot" if settings.market == "spot" else "linear"

    def _has_private_auth(self) -> bool:
        return bool(settings.bybit_api_key and settings.bybit_api_secret)

    async def _get_server_time_ms(self) -> int:
        """GET /v5/market/time (no auth). Returns Bybit server time in milliseconds for request signing.
        API: https://bybit-exchange.github.io/docs/v5/market/time"""
        base = settings.bybit_rest_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base}/v5/market/time")
            response.raise_for_status()
        data = response.json()
        # Top-level "time" is server time in ms; fallback to result.timeSecond * 1000
        if "time" in data:
            return int(data["time"])
        result = data.get("result", {})
        sec = result.get("timeSecond", "0")
        return int(sec) * 1000

    def _sign_request(
        self, method: str, timestamp_ms: int, query_string: str, body: str
    ) -> dict[str, str]:
        """Build auth headers for Bybit v5 private REST. GET: sign timestamp+apiKey+recvWindow+queryString; POST: +body."""
        recv = str(settings.bybit_recv_window)
        if method.upper() == "GET":
            sign_str = f"{timestamp_ms}{settings.bybit_api_key}{recv}{query_string}"
        else:
            sign_str = f"{timestamp_ms}{settings.bybit_api_key}{recv}{body}"
        sig = hmac.new(
            settings.bybit_api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": settings.bybit_api_key,
            "X-BAPI-TIMESTAMP": str(timestamp_ms),
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sig,
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Send authenticated private REST request. Raises BybitClientError on retCode != 0."""
        if not self._has_private_auth():
            raise BybitClientError("Bybit API key/secret not configured")
        base = settings.bybit_rest_base_url.rstrip("/")
        url = f"{base}{path}"
        query_string = ""
        body_str = ""
        if params:
            query_string = urlencode(sorted(params.items()))
        if json_body is not None:
            body_str = json.dumps(json_body, separators=(",", ":"))
        # Use Bybit server time for all private requests to avoid recv_window timestamp errors.
        timestamp_ms = await self._get_server_time_ms()
        headers = {
            "Content-Type": "application/json",
            **self._sign_request(method, timestamp_ms, query_string, body_str),
        }
        if method.upper() == "GET":
            full_url = f"{url}?{query_string}" if query_string else url
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(full_url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    content=body_str.encode("utf-8") if body_str else None,
                    headers=headers,
                )
        response.raise_for_status()
        data = response.json()
        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise BybitClientError(
                data.get("retMsg", "Unknown error"),
                ret_code=ret_code,
                ret_msg=data.get("retMsg"),
            )
        return data

    def _ws_public_url(self) -> str:
        """Return correct public WS URL for current market."""
        return (
            settings.bybit_ws_public_spot_url
            if settings.market == "spot"
            else settings.bybit_ws_public_linear_url
        )

    async def list_spot_symbols(self) -> list[SymbolInfo]:
        """[Bybit] REST GET /v5/market/instruments-info. Returns tradable Spot symbols.

        For Spot, Bybit does not use pagination on this endpoint; it returns the full list
        of instruments for `category=spot` in a single response.
        """
        category = self._market_category()
        url = f"{settings.bybit_rest_base_url}/v5/market/instruments-info"
        params = {"category": category}
        logger.info(
            "BybitClient.list_spot_symbols: url=%s category=%s market_setting=%s",
            url,
            category,
            settings.market,
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        raw_symbols = payload.get("result", {}).get("list", [])
        symbols: list[SymbolInfo] = []
        for item in raw_symbols:
            symbols.append(
                SymbolInfo(
                    symbol=item["symbol"],
                    baseCoin=item["baseCoin"],
                    quoteCoin=item["quoteCoin"],
                    status=item["status"],
                )
            )
        return symbols

    async def get_instrument_info(self, *, symbol: str) -> dict:
        """Return raw instruments-info record for a single symbol (current market category).

        Uses GET /v5/market/instruments-info with category derived from settings.market.
        """
        category = self._market_category()
        url = f"{settings.bybit_rest_base_url}/v5/market/instruments-info"
        params = {
            "category": category,
            "symbol": symbol.upper(),
        }
        logger.info(
            "BybitClient.get_instrument_info: url=%s category=%s symbol=%s market_setting=%s",
            url,
            category,
            symbol.upper(),
            settings.market,
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        items = payload.get("result", {}).get("list", [])
        if not items:
            return {}
        # Bybit returns a list; for a specific symbol we expect at most one item.
        return items[0]

    async def get_tick_size(self, *, symbol: str) -> float | None:
        """Return the exchange tick size (minimum price increment) for the symbol, or None.

        Reads priceFilter.tickSize from instruments-info. If unavailable or unparsable,
        returns None so callers can fall back to a heuristic.
        """
        info = await self.get_instrument_info(symbol=symbol)
        price_filter = info.get("priceFilter") or {}
        tick = price_filter.get("tickSize")
        if tick is None:
            return None
        try:
            return float(tick)
        except (TypeError, ValueError):
            return None

    async def get_tickers(self, symbols: list[str] | None = None) -> list[TickerSnapshot]:
        """[Bybit] REST GET /v5/market/tickers. Returns 24h snapshots for ticker list."""
        url = f"{settings.bybit_rest_base_url}/v5/market/tickers"
        params = {"category": self._market_category()}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        raw_tickers = payload.get("result", {}).get("list", [])
        requested = {item.upper() for item in symbols} if symbols else None
        snapshots: list[TickerSnapshot] = []
        for row in raw_tickers:
            symbol = row.get("symbol", "")
            if requested and symbol.upper() not in requested:
                continue
            snapshots.append(
                TickerSnapshot(
                    symbol=symbol,
                    price=float(row.get("lastPrice", 0.0)),
                    change_24h_percent=float(row.get("price24hPcnt", 0.0)) * 100.0,
                    volume_24h=float(row.get("volume24h", 0.0)),
                )
            )
        return snapshots

    async def stream_ticker(self, symbol: str) -> AsyncGenerator[TickerTick, None]:
        """[Bybit] WebSocket tickers.{symbol}. Streams lastPrice, volume24h, change%.
        For ticker list only; do not use for chart bar updates."""
        topic = f"tickers.{symbol}"
        async with websockets.connect(self._ws_public_url()) as connection:
            subscribe_message = json.dumps({"op": "subscribe", "args": [topic]})
            await connection.send(subscribe_message)

            async for message in connection:
                data = json.loads(message)
                raw_payload = data.get("data")
                if raw_payload is None:
                    continue
                if isinstance(raw_payload, list):
                    if not raw_payload:
                        continue
                    row = raw_payload[0]
                else:
                    row = raw_payload
                tick = TickerTick(
                    symbol=row.get("symbol", symbol),
                    price=float(row.get("lastPrice", 0.0)),
                    change_24h_percent=float(row.get("price24hPcnt", 0.0)) * 100.0,
                    volume_24h=float(row.get("volume24h", 0.0)),
                    ts=int(data.get("ts", 0)),
                )
                # Skip invalid ticks (linear stream often sends zero price/volume)
                if tick.price <= 0.0:
                    continue
                yield tick
    
    async def get_klines(
        self, symbol: str, interval: str = "1", limit: int = 300
    ) -> list[Candle]:
        """[Bybit] REST GET /v5/market/kline. Returns historical OHLCV candles for chart.
        Bybit max per request is 1000; for limit>1000 we fetch in batches and merge."""
        BYBIT_MAX = 1000
        category = self._market_category()
        logger.info(
            "BybitClient.get_klines: symbol=%s interval=%s limit=%d category=%s market_setting=%s",
            symbol,
            interval,
            limit,
            category,
            settings.market,
        )
        all_candles: list[Candle] = []
        remaining = limit
        end_time: int | None = None

        while remaining > 0:
            batch_limit = min(remaining, BYBIT_MAX)
            url = f"{settings.bybit_rest_base_url}/v5/market/kline"
            params: dict = {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": batch_limit,
            }
            if end_time is not None:
                params["end"] = end_time
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
            payload = response.json()
            raw_klines = payload.get("result", {}).get("list", [])
            if not raw_klines:
                break
            batch: list[Candle] = []
            for kline in reversed(raw_klines):
                batch.append(
                    Candle(
                        time=int(kline[0]),
                        open=float(kline[1]),
                        high=float(kline[2]),
                        low=float(kline[3]),
                        close=float(kline[4]),
                        volume=float(kline[5]),
                    )
                )
            all_candles = batch + all_candles
            remaining -= len(batch)
            if len(batch) < batch_limit:
                break
            end_time = batch[0].time - 1
        if all_candles:
            first_c = all_candles[0]
            last_c = all_candles[-1]
            logger.info(
                "BybitClient.get_klines: assembled %d candles symbol=%s interval=%s category=%s "
                "first={time=%s open=%s high=%s low=%s close=%s volume=%s} "
                "last={time=%s open=%s high=%s low=%s close=%s volume=%s}",
                len(all_candles),
                symbol,
                interval,
                category,
                getattr(first_c, "time", None),
                getattr(first_c, "open", None),
                getattr(first_c, "high", None),
                getattr(first_c, "low", None),
                getattr(first_c, "close", None),
                getattr(first_c, "volume", None),
                getattr(last_c, "time", None),
                getattr(last_c, "open", None),
                getattr(last_c, "high", None),
                getattr(last_c, "low", None),
                getattr(last_c, "close", None),
                getattr(last_c, "volume", None),
            )
        return all_candles
    async def stream_kline(
        self, symbol: str, interval: str
    ) -> AsyncGenerator[BarUpdate, None]:
        """[Bybit] WebSocket kline.{interval}.{symbol}. Streams current bar OHLCV updates.
        confirm=false while bar is open; confirm=true when closed. Uses market-specific stream."""
        topic = f"kline.{interval}.{symbol}"
        async with websockets.connect(self._ws_public_url()) as connection:
            subscribe_message = json.dumps({"op": "subscribe", "args": [topic]})
            await connection.send(subscribe_message)

            async for message in connection:
                data = json.loads(message)
                raw_payload = data.get("data")
                if raw_payload is None:
                    continue
                if isinstance(raw_payload, list):
                    if not raw_payload:
                        continue
                    row = raw_payload[0]
                else:
                    row = raw_payload
                yield BarUpdate(
                    start=int(row["start"]),
                    end=int(row["end"]),
                    open=float(row["open"]),
                    close=float(row["close"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    volume=float(row["volume"]),
                    confirm=bool(row.get("confirm", True)),
                    timestamp=int(row.get("timestamp", data.get("ts", 0))),
                )

    # ---------- Private REST (orders, positions, wallet) ----------

    async def create_order(
        self,
        *,
        category: str,
        symbol: str,
        side: str,
        orderType: str,
        qty: str | float,
        price: str | float | None = None,
        **kwargs: str | float | None,
    ) -> dict:
        """POST /v5/order/create. category: spot | linear. side: Buy | Sell. orderType: Market | Limit.
        API: https://bybit-exchange.github.io/docs/v5/order/create-order"""
        body: dict = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": orderType,
            "qty": str(qty) if not isinstance(qty, str) else qty,
        }
        if price is not None:
            body["price"] = str(price) if not isinstance(price, str) else price
        for k, v in kwargs.items():
            if v is not None:
                body[k] = v
        data = await self._request("POST", "/v5/order/create", json_body=body)
        return data.get("result", data)

    async def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        orderId: str | None = None,
        orderLinkId: str | None = None,
    ) -> dict:
        """POST /v5/order/cancel. Provide either orderId or orderLinkId.
        API: https://bybit-exchange.github.io/docs/v5/order/cancel-order"""
        body: dict = {"category": category, "symbol": symbol}
        if orderId is not None:
            body["orderId"] = orderId
        if orderLinkId is not None:
            body["orderLinkId"] = orderLinkId
        data = await self._request("POST", "/v5/order/cancel", json_body=body)
        return data.get("result", data)

    async def get_open_orders(self, *, category: str, symbol: str) -> list[dict]:
        """GET /v5/order/realtime. Returns list of open orders.
        API: https://bybit-exchange.github.io/docs/v5/order/open-order"""
        data = await self._request(
            "GET",
            "/v5/order/realtime",
            params={"category": category, "symbol": symbol},
        )
        return data.get("result", {}).get("list", [])

    async def get_wallet_balance(
        self,
        *,
        accountType: str = "UNIFIED",
        coin: str | None = None,
    ) -> dict:
        """GET /v5/account/wallet-balance.
        API: https://bybit-exchange.github.io/docs/v5/account/wallet-balance"""
        params: dict = {"accountType": accountType}
        if coin is not None:
            params["coin"] = coin
        data = await self._request("GET", "/v5/account/wallet-balance", params=params)
        return data.get("result", {})

    async def get_linear_positions(self, *, symbol: str) -> list[dict]:
        """GET /v5/position/list. category=linear. Returns list of position objects.
        API: https://bybit-exchange.github.io/docs/v5/position/position-info"""
        data = await self._request(
            "GET",
            "/v5/position/list",
            params={"category": "linear", "symbol": symbol},
        )
        return data.get("result", {}).get("list", [])

    async def set_linear_trading_stop(
        self,
        *,
        symbol: str,
        stopLoss: float | None = None,
        takeProfit: float | None = None,
        trailingStop: str | None = None,
        slTriggerBy: str | None = None,
        tpTriggerBy: str | None = None,
        **kwargs: str | float | None,
    ) -> dict:
        """POST /v5/position/trading-stop. Set SL/TP/TS on linear position.
        API: https://bybit-exchange.github.io/docs/v5/position/trading-stop"""
        body: dict = {"symbol": symbol, "category": "linear"}
        if stopLoss is not None:
            body["stopLoss"] = str(stopLoss)
        if takeProfit is not None:
            body["takeProfit"] = str(takeProfit)
        if trailingStop is not None:
            body["trailingStop"] = trailingStop
        if slTriggerBy is not None:
            body["slTriggerBy"] = slTriggerBy
        if tpTriggerBy is not None:
            body["tpTriggerBy"] = tpTriggerBy
        for k, v in kwargs.items():
            if v is not None:
                body[k] = v
        data = await self._request("POST", "/v5/position/trading-stop", json_body=body)
        return data.get("result", data)

    async def set_linear_leverage(
        self,
        *,
        symbol: str,
        buyLeverage: str | int,
        sellLeverage: str | int | None = None,
    ) -> dict:
        """POST /v5/position/set-leverage. Set leverage for linear (one-way: buyLeverage=sellLeverage).
        API: https://bybit-exchange.github.io/docs/v5/position/leverage"""
        body: dict = {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(buyLeverage),
            "sellLeverage": str(sellLeverage if sellLeverage is not None else buyLeverage),
        }
        data = await self._request("POST", "/v5/position/set-leverage", json_body=body)
        return data.get("result", data)
