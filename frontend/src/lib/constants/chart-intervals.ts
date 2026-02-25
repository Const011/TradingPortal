/**
 * Chart range / candle intervals supported by the backend (Bybit kline).
 * value = API param for /candles?interval=
 */
export type ChartIntervalValue =
  | "1"
  | "5"
  | "15"
  | "60"
  | "240"
  | "D"
  | "W"
  | "M";

export type ChartIntervalOption = {
  label: string;
  value: ChartIntervalValue;
};

export const CHART_INTERVAL_OPTIONS: ChartIntervalOption[] = [
  { label: "1m", value: "1" },
  { label: "5m", value: "5" },
  { label: "15m", value: "15" },
  { label: "1h", value: "60" },
  { label: "4h", value: "240" },
  { label: "1D", value: "D" },
  { label: "1W", value: "W" },
  { label: "1M", value: "M" },
];

export const DEFAULT_CHART_INTERVAL: ChartIntervalValue = "1";
