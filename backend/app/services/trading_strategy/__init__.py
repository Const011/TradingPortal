"""Trading strategy module — produces trade events from candles and indicators."""

from app.services.trading_strategy.types import TradeEvent
from app.services.trading_strategy.order_block_trend_following import (
    compute_order_block_trend_following,
)

__all__ = [
    "TradeEvent",
    "compute_order_block_trend_following",
]
