/**
 * Chart time utilities for Lightweight Charts.
 * The library treats all times as UTC. To display local time, we convert UTC timestamps
 * to "local-adjusted" values before passing to the chart (see lightweight-charts time-zones docs).
 */

/**
 * Convert UTC timestamp to local-adjusted seconds for chart display.
 * Use this when passing candle/volume/overlay times to the chart so the axis shows local time.
 * @param msOrSec - Timestamp in ms (if >= 1e12) or seconds
 */
export function toChartTimeLocal(msOrSec: number): number {
  const sec = msOrSec >= 1e12 ? Math.floor(msOrSec / 1000) : msOrSec;
  const d = new Date(sec * 1000);
  return Math.floor(
    Date.UTC(
      d.getFullYear(),
      d.getMonth(),
      d.getDate(),
      d.getHours(),
      d.getMinutes(),
      d.getSeconds(),
      d.getMilliseconds()
    ) / 1000
  );
}
