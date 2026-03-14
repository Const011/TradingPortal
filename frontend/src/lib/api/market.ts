/** Market API: calls our backend. Backend proxies Bybit. */
import {
  Candle,
  StrategySignalsData,
  SymbolInfo,
  TickerSnapshot,
  VolumeProfileData,
  SupportResistanceData,
  OrderBlocksData,
  SmartMoneyStructureData,
} from "@/lib/types/market";

/** Backend mode: simulation (stream strategy) or trading (trade log). */
export type BackendMode = "simulation" | "trading";

/** Gateway config from GET /api/v1/mode. */
export type GatewayConfig = {
  mode: BackendMode;
  symbol?: string;
  interval?: string;
  bars_window?: number;
};

/** Fetch backend mode and gateway config. When mode=trading, returns symbol, interval, bars_window. */
export async function fetchGatewayConfig(
  backendBaseUrl: string
): Promise<{ mode: BackendMode; symbol?: string; interval?: string; bars_window?: number }> {
  const res = await fetch(`${backendBaseUrl}/api/v1/mode`);
  if (!res.ok) return { mode: "simulation" };
  const data = (await res.json()) as {
    mode: string;
    symbol?: string;
    interval?: string;
    bars_window?: number;
  };
  return {
    mode: data.mode === "trading" ? "trading" : "simulation",
    symbol: data.symbol,
    interval: data.interval,
    bars_window: data.bars_window,
  };
}

/** @deprecated Use fetchGatewayConfig */
export async function fetchBackendMode(backendBaseUrl: string): Promise<BackendMode> {
  const { mode } = await fetchGatewayConfig(backendBaseUrl);
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
  backendBaseUrl: string,
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
export async function fetchSymbols(backendBaseUrl: string): Promise<SymbolInfo[]> {
  const response = await fetch(`${backendBaseUrl}/api/v1/symbols`);
  if (!response.ok) {
    throw new Error("Failed to fetch symbols");
  }
  return (await response.json()) as SymbolInfo[];
}

/** [Backend] GET /candles. Fetches historical klines; used for initial chart load or standalone fetch. */
export async function fetchCandles(
  backendBaseUrl: string,
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
export async function fetchTickers(
  backendBaseUrl: string,
  symbols: string[]
): Promise<TickerSnapshot[]> {
  const searchParams = new URLSearchParams();
  if (symbols.length > 0) {
    searchParams.set("symbols", symbols.join(","));
  }
  const url = `${backendBaseUrl}/api/v1/tickers?${searchParams.toString()}`;
  const maxAttempts = 5;
  let lastStatus = 0;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const response = await fetch(url);
    lastStatus = response.status;
    if (response.ok) {
      return (await response.json()) as TickerSnapshot[];
    }
    if (response.status !== 503 && response.status !== 502 && response.status !== 504) {
      throw new Error(`Failed to fetch tickers (${response.status})`);
    }
    const delayMs = Math.min(30_000, 1000 * 2 ** (attempt - 1));
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error(`Failed to fetch tickers after retries (last status ${lastStatus})`);
}

/** [Backend] WS /stream/ticks. Ticker stream for ticker list only (lastPrice, volume24h, change%). */
export function getTicksWebSocketUrl(backendBaseUrl: string, symbol: string): string {
  const normalizedBaseUrl = backendBaseUrl.replace("https://", "wss://").replace(
    "http://",
    "ws://"
  );
  return `${normalizedBaseUrl}/api/v1/stream/ticks/${symbol}`;
}

export type StrategyMarkersMode = "off" | "simulation" | "trade";

/** [Backend] WS /stream/candles. Merged snapshot + live bar updates + indicators; use for chart data. */
export function getCandlesWebSocketUrl(
  backendBaseUrl: string,
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

export type PreciseSimulationResponse = {
  symbol: string;
  interval: string;
  candles: Candle[];
  graphics?: {
    volumeProfile?: VolumeProfileData;
    supportResistance?: SupportResistanceData;
    orderBlocks?: OrderBlocksData;
    smartMoney?: { structure?: SmartMoneyStructureData };
    strategySignals?: StrategySignalsData | null;
  };
};

/** [Backend] POST /strategies/{strategyId}/simulate-precise. Run precise simulation and return full snapshot-like payload. */
export async function runPreciseSimulationApi(
  backendBaseUrl: string,
  strategyId: string,
  symbol: string,
  interval: string,
  limit: number,
  volumeProfileWindow: number
): Promise<PreciseSimulationResponse> {
  const res = await fetch(
    `${backendBaseUrl}/api/v1/strategies/${strategyId}/simulate-precise`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol,
        interval,
        limit,
        volume_profile_window: volumeProfileWindow,
      }),
    }
  );
  if (!res.ok) {
    throw new Error("Precise simulation failed");
  }
  const data = (await res.json()) as PreciseSimulationResponse;
  return data;
}

