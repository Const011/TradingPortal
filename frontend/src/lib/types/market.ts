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

