"""Chart-agnostic graphics primitives. Frontend maps each type to Lightweight Charts drawing."""

from typing import Literal

HorizontalLineType = Literal["horizontalLine"]
BoxType = Literal["box"]
ExtendOption = Literal["left", "right", "both", "none"]
LineStyle = Literal["solid", "dashed", "dotted"]


def box(
    top_left: dict,
    bottom_right: dict,
    fill_color: str,
    border_color: str | None = None,
    extend: ExtendOption = "none",
) -> dict:
    """Build a box primitive. top_left/bottom_right: { time: int, price: float }."""
    return {
        "type": "box",
        "topLeft": top_left,
        "bottomRight": bottom_right,
        "fillColor": fill_color,
        "borderColor": border_color,
        "extend": extend,
    }


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
