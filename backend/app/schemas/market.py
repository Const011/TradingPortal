from pydantic import BaseModel, Field


class SymbolInfo(BaseModel):
    symbol: str
    base_coin: str = Field(alias="baseCoin")
    quote_coin: str = Field(alias="quoteCoin")
    status: str


class Candle(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class TickerTick(BaseModel):
    symbol: str
    price: float
    change_24h_percent: float
    volume_24h: float
    ts: int


class TickerSnapshot(BaseModel):
    symbol: str
    price: float
    change_24h_percent: float
    volume_24h: float


class BarUpdate(BaseModel):
    """Real-time kline update from Bybit (current bar OHLCV; confirm=false while bar is open)."""

    start: int
    end: int
    open: float
    close: float
    high: float
    low: float
    volume: float
    confirm: bool
    timestamp: int

