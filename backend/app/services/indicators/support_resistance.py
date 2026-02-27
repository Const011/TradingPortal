"""Support/resistance levels from volume profile minima."""

from app.schemas.chart_primitives import horizontal_line

DEFAULT_VICINITY = 9
DEFAULT_SMOOTHING_WINDOW = 8
DEFAULT_WIDTH_MULTIPLIER = 1.0
DEFAULT_MAX_WIDTH = 10
DEFAULT_SR_COLOR = "rgba(51, 33, 243, 0.24)"


def smooth_triangular(values: list[float], window_size: int) -> list[float]:
    """Triangular-weighted moving average. Handles boundaries by clamping."""
    n = len(values)
    if n == 0:
        return []
    w = max(3, window_size if window_size % 2 == 1 else window_size + 1)
    half = w // 2
    weights = [half + 1 - abs(j - half) for j in range(w)]
    total = sum(weights)
    weights = [x / total for x in weights]
    out: list[float] = []
    for i in range(n):
        s = 0.0
        for j, wj in enumerate(weights):
            idx = i + j - half
            idx = max(0, min(n - 1, idx))
            s += values[idx] * wj
        out.append(s)
    return out


def compute_support_resistance_lines(
    profile: list[dict],
    vicinity: int = DEFAULT_VICINITY,
    smoothing_window: int = DEFAULT_SMOOTHING_WINDOW,
    width_multiplier: float = DEFAULT_WIDTH_MULTIPLIER,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> list[dict]:
    """
    Find local minima in (smoothed) volume profile and return horizontal line primitives.

    Profile must be sorted by price descending (as from volume_profile).
    Returns lines with width proportional to strength (lower minimum -> stronger level).
    """
    n = len(profile)
    if n < 2 * vicinity + 1:
        return []

    vols = [p["vol"] for p in profile]
    smoothed = smooth_triangular(vols, smoothing_window)

    minima_indices: list[int] = []
    for i in range(vicinity, n - vicinity):
        v = smoothed[i]
        is_min = True
        for j in range(i - vicinity, i + vicinity + 1):
            if j != i and smoothed[j] <= v:
                is_min = False
                break
        if is_min:
            minima_indices.append(i)

    lines: list[dict] = []

    for k in minima_indices:
        minima_vol = max(smoothed[k], 1e-10)

        left_start = 0
        for prev in reversed(minima_indices):
            if prev < k:
                left_start = prev + 1
                break

        right_end = n - 1
        for nxt in minima_indices:
            if nxt > k:
                right_end = nxt - 1
                break

        left_sum = sum(smoothed[i] for i in range(left_start, k))
        left_size = k - left_start
        right_sum = sum(smoothed[i] for i in range(k + 1, right_end + 1))
        right_size = right_end - k

        if left_size + right_size == 0:
            clusters_avg = 0.0
        else:
            clusters_avg = (left_sum + right_sum) / (left_size + right_size)

        volume_ratio = clusters_avg / minima_vol
        raw_width = volume_ratio * width_multiplier
        line_width = max(1.0, min(raw_width, max_width))

        price = profile[k]["price"]
        lines.append(
            horizontal_line(
                price=price,
                width=line_width,
                extend="both",
                color=DEFAULT_SR_COLOR,
                style="solid",
            )
        )

    return lines
