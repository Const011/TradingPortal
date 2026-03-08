"""Types for the execution service: entry intent and executor response."""

from dataclasses import dataclass


@dataclass
class ExecutorEntryResponse:
    """Response from executor after submitting an entry order."""

    order_received: bool
    entry_yet: bool  # True only after executor has confirmed fill and written current.json
    order_id: str | None = None
    order_link_id: str | None = None
    message: str | None = None  # Error or info
