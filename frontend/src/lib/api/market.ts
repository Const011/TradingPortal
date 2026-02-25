/** Market API: calls our backend. Backend proxies Bybit. */
import { Candle, SymbolInfo, TickerSnapshot } from "@/lib/types/market";

const backendBaseUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

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

/** [Backend] WS /stream/candles. Merged snapshot + live bar updates; use for chart data. */
export function getCandlesWebSocketUrl(symbol: string, interval: string): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  return `${normalizedBaseUrl}/api/v1/stream/candles/${symbol}?interval=${encodeURIComponent(interval)}`;
}

