import { Candle, SymbolInfo, TickerSnapshot } from "@/lib/types/market";

const backendBaseUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export async function fetchSymbols(): Promise<SymbolInfo[]> {
  const response = await fetch(`${backendBaseUrl}/api/v1/symbols`);
  if (!response.ok) {
    throw new Error("Failed to fetch symbols");
  }
  return (await response.json()) as SymbolInfo[];
}

export async function fetchCandles(symbol: string, interval: string): Promise<Candle[]> {
  const searchParams = new URLSearchParams({
    symbol,
    interval,
    limit: "300",
  });
  const response = await fetch(`${backendBaseUrl}/api/v1/candles?${searchParams.toString()}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch candles for ${symbol}`);
  }
  return (await response.json()) as Candle[];
}

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

export function getTicksWebSocketUrl(symbol: string): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  return `${normalizedBaseUrl}/api/v1/stream/ticks/${symbol}`;
}

export function getBarUpdatesWebSocketUrl(symbol: string, interval: string): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  return `${normalizedBaseUrl}/api/v1/stream/bar-updates/${symbol}?interval=${encodeURIComponent(interval)}`;
}

