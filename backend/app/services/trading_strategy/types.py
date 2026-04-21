"""Trading strategy types."""

from dataclasses import dataclass


@dataclass
class TradeEvent:
    """A trade signal emitted by a strategy."""

    time: int  # Unix seconds (candle close time)
    trade_id: str  # Stable identifier for this trade across events/segments
    bar_index: int  # Index in candles list
    type: str  # e.g. OB_TREND_BUY, OB_TREND_SELL
    side: str | None  # "long" | "short" | None
    price: float  # Entry price
    target_price: float | None  # Optional; for close-on-target orders
    initial_stop_price: float  # Required; no orders without stop
    context: dict  # type-specific: ob_top, ob_bottom, ob_founding_bar, etc.


@dataclass
class StopSegment:
    """A horizontal segment showing the stop level over a time range."""

    start_time: int  # Unix seconds
    end_time: int  # Unix seconds
    trade_id: str
    price: float
    side: str  # "long" | "short"


@dataclass
class StrategySeedPosition:
    """Open trade state restored from the trade log for live trailing."""

    trade_id: str
    side: str
    entry_time: int
    entry_price: float
    stop_price: float
    target_price: float | None
    active_stop_time: int
    reference_stop_price: float
    reference_stop_time: int
