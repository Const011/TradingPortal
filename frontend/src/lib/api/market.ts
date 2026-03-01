/** Market API: calls our backend. Backend proxies Bybit. */
import { Candle, SymbolInfo, TickerSnapshot } from "@/lib/types/market";

/** Backend URL. Set by run-dev-*.sh; or use .env NEXT_PUBLIC_API_URL for manual runs. */
const backendBaseUrl =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:9000";

/** Backend mode: simulation (stream strategy) or trading (trade log). From env or fetch. */
export type BackendMode = "simulation" | "trading";

export const backendMode: BackendMode =
  (process.env.NEXT_PUBLIC_MODE as BackendMode) ?? "simulation";

/** Gateway config when mode=trading (symbol and interval are fixed). */
export type GatewayConfig = {
  mode: "trading";
  symbol: string;
  interval: string;
};

/** Fetch backend mode and gateway config. When mode=trading, returns symbol and interval. */
export async function fetchGatewayConfig(): Promise<{
  mode: BackendMode;
  symbol?: string;
  interval?: string;
}> {
  const res = await fetch(`${backendBaseUrl}/api/v1/mode`);
  if (!res.ok) return { mode: "simulation" };
  const data = (await res.json()) as { mode: string; symbol?: string; interval?: string };
  return {
    mode: data.mode === "trading" ? "trading" : "simulation",
    symbol: data.symbol,
    interval: data.interval,
  };
}

/** @deprecated Use fetchGatewayConfig */
export async function fetchBackendMode(): Promise<BackendMode> {
  const { mode } = await fetchGatewayConfig();
  return mode;
}

/** Trade from trade-log API (mode=trading). */
export type TradeLogTrade = {
  tradeId: string;
  entryDateTime: string;
  side: "long" | "short";
  entryPrice: number;
  closeDateTime: string;
  closePrice: number;
  closeReason: string;
  points: number;
  markers: Array<{ time: number; position: "above" | "below"; shape: string; color: string }>;
  stopSegments: Array<{ startTime: number; endTime: number; price: number; side: string }>;
  stopLines: Array<{ type: string; from: { time: number; price: number }; to: { time: number; price: number }; color: string; width?: number; style?: string }>;
  events: Array<{ time: number; barIndex: number; type: string; side: string; price: number; targetPrice?: number; initialStopPrice: number; context?: Record<string, unknown> }>;
};

/** [Backend] GET /trade-log. Used when mode=trading for chart and results. */
export async function fetchTradeLog(
  symbol: string,
  interval: string,
  since?: number
): Promise<{ mode: string; trades: TradeLogTrade[] }> {
  const params = new URLSearchParams({ symbol, interval });
  if (since != null) params.set("since", String(since));
  const res = await fetch(`${backendBaseUrl}/api/v1/trade-log?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch trade log for ${symbol}`);
  return res.json() as Promise<{ mode: string; trades: TradeLogTrade[] }>;
}

/** [Backend] GET /symbols. Fetches tradable symbols for selector and ticker list. */
export async function fetchSymbols(): Promise<SymbolInfo[]> {
  const response = await fetch(`${backendBaseUrl}/api/v1/symbols`);
  if (!response.ok) {
    throw new Error("Failed to fetch symbols");
  }
  return (await response.json()) as SymbolInfo[];
}

/** [Backend] GET /candles. Fetches historical klines; used for initial chart load or standalone fetch. */
export async function fetchCandles(
  symbol: string,
  interval: string,
  limit: number = 2000
): Promise<Candle[]> {
  const searchParams = new URLSearchParams({
    symbol,
    interval,
    limit: String(Math.min(2000, Math.max(50, limit))),
  });
  const response = await fetch(`${backendBaseUrl}/api/v1/candles?${searchParams.toString()}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch candles for ${symbol}`);
  }
  return (await response.json()) as Candle[];
}

/** [Backend] GET /tickers. Fetches 24h snapshots for ticker list (not for chart). */
export async function fetchTickers(symbols: string[]): Promise<TickerSnapshot[]> {
  const searchParams = new URLSearchParams();
  if (symbols.length > 0) {
    searchParams.set("symbols", symbols.join(","));
  }
  const response = await fetch(`${backendBaseUrl}/api/v1/tickers?${searchParams.toString()}`);
  if (!response.ok) {
    throw new Error("Failed to fetch tickers");
  }
  return (await response.json()) as TickerSnapshot[];
}

/** [Backend] WS /stream/ticks. Ticker stream for ticker list only (lastPrice, volume24h, change%). */
export function getTicksWebSocketUrl(symbol: string): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  return `${normalizedBaseUrl}/api/v1/stream/ticks/${symbol}`;
}

export type StrategyMarkersMode = "off" | "simulation" | "trade";

/** [Backend] WS /stream/candles. Merged snapshot + live bar updates + indicators; use for chart data. */
export function getCandlesWebSocketUrl(
  symbol: string,
  interval: string,
  volumeProfileWindow: number = 2000,
  strategyMarkers: StrategyMarkersMode = "off"
): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  const params = new URLSearchParams({
    interval,
    volume_profile_window: String(volumeProfileWindow),
    strategy_markers: strategyMarkers,
  });
  return `${normalizedBaseUrl}/api/v1/stream/candles/${symbol}?${params.toString()}`;
}

