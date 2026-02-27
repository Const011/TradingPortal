export type SymbolInfo = {
  symbol: string;
  baseCoin: string;
  quoteCoin: string;
  status: string;
};

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type TickerTick = {
  symbol: string;
  price: number;
  change_24h_percent: number;
  volume_24h: number;
  ts: number;
};

export type TickerSnapshot = {
  symbol: string;
  price: number;
  change_24h_percent: number;
  volume_24h: number;
};

/** Current (last) bar OHLC + volume, aligned with chart data; close/high/low updated by latest tick. */
export type CurrentBar = {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Volume profile data point (price level + weighted volume). */
export type VolumeProfileDataPoint = {
  price: number;
  vol: number;
};

/** Volume profile indicator data (from backend). */
export type VolumeProfileData = {
  time: number;
  profile: VolumeProfileDataPoint[];
  width: number;
};

/** Horizontal line primitive (S/R, etc.). */
export type HorizontalLineData = {
  type: "horizontalLine";
  price: number;
  width: number;
  extend: "left" | "right" | "both";
  color?: string;
  style?: "solid" | "dashed" | "dotted";
};

/** Support/resistance lines from backend. */
export type SupportResistanceData = {
  lines: HorizontalLineData[];
};

/** Graphics object in stream payload (volume profile + S/R primitives). */
export type GraphicsData = {
  volumeProfile?: VolumeProfileData;
  supportResistance?: SupportResistanceData;
};

/** Real-time kline update (current bar OHLCV; confirm=false while bar is open). */
export type BarUpdate = {
  start: number;
  end: number;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  confirm: boolean;
  timestamp: number;
};

