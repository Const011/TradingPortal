"""Chart-agnostic graphics primitives. Frontend maps each type to Lightweight Charts drawing."""

from typing import Literal

HorizontalLineType = Literal["horizontalLine"]
ExtendOption = Literal["left", "right", "both"]
LineStyle = Literal["solid", "dashed", "dotted"]


def horizontal_line(
    price: float,
    width: float = 2,
    extend: ExtendOption = "both",
    color: str | None = None,
    style: LineStyle = "solid",
) -> dict:
    """Build a horizontal line primitive."""
    return {
        "type": "horizontalLine",
        "price": price,
        "width": width,
        "extend": extend,
        "color": color,
        "style": style,
    }
