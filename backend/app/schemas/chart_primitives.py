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


def line_segment(
    from_pt: dict,
    to_pt: dict,
    color: str,
    width: float = 1,
    style: LineStyle = "solid",
) -> dict:
    """Build a line segment primitive. from_pt/to_pt: { time: int, price: float }."""
    return {
        "type": "lineSegment",
        "from": from_pt,
        "to": to_pt,
        "color": color,
        "width": width,
        "style": style,
    }


def label(
    time: int,
    price: float,
    text: str,
    color: str,
    style: Literal["up", "down"] = "up",
    size: Literal["tiny", "small", "normal"] = "small",
) -> dict:
    """Build a label primitive anchored to time/price."""
    return {
        "type": "label",
        "time": time,
        "price": price,
        "text": text,
        "color": color,
        "style": style,
        "size": size,
    }
