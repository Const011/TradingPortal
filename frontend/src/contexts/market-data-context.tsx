"use client";

import {
  createContext,
  ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  DEFAULT_CHART_INTERVAL,
  type ChartIntervalValue,
} from "@/lib/constants/chart-intervals";
import {
  getStoredChartPreferences,
  setStoredChartPreferences,
} from "@/lib/chart-preferences-storage";
import {
  fetchSymbols,
  fetchTickers,
  getCandlesWebSocketUrl,
  getTicksWebSocketUrl,
} from "@/lib/api/market";
import {
  Candle,
  type CurrentBar,
  SymbolInfo,
  TickerSnapshot,
  TickerTick,
} from "@/lib/types/market";

type MarketDataContextValue = {
  symbols: SymbolInfo[];
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
  chartInterval: ChartIntervalValue;
  setChartInterval: (interval: ChartIntervalValue) => void;
  autoScaleEnabled: boolean;
  setAutoScaleEnabled: (enabled: boolean) => void;
  logScaleEnabled: boolean;
  setLogScaleEnabled: (enabled: boolean) => void;
  volumeProfileEnabled: boolean;
  setVolumeProfileEnabled: (enabled: boolean) => void;
  candles: Candle[];
  currentBar: CurrentBar | null;
  hoveredBarTime: number | null;
  setHoveredBarTime: (time: number | null) => void;
  tickers: Record<string, TickerSnapshot>;
  latestTick: TickerTick | null;
  loading: boolean;
  error: string | null;
};

const MarketDataContext = createContext<MarketDataContextValue | null>(null);

type MarketDataProviderProps = {
  children: ReactNode;
};

export function MarketDataProvider({ children }: MarketDataProviderProps) {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const [chartInterval, setChartInterval] = useState<ChartIntervalValue>(DEFAULT_CHART_INTERVAL);
  const [autoScaleEnabled, setAutoScaleEnabled] = useState<boolean>(true);
  const [logScaleEnabled, setLogScaleEnabled] = useState<boolean>(false);
  const [volumeProfileEnabled, setVolumeProfileEnabled] = useState<boolean>(false);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [tickers, setTickers] = useState<Record<string, TickerSnapshot>>({});
  const [latestTick, setLatestTick] = useState<TickerTick | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredBarTime, setHoveredBarTime] = useState<number | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const candleSocketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const prefs = getStoredChartPreferences();
    setSelectedSymbol(prefs.selectedSymbol);
    setChartInterval(prefs.chartInterval);
    setAutoScaleEnabled(prefs.autoScale);
    setLogScaleEnabled(prefs.logScale);
    setVolumeProfileEnabled(prefs.volumeProfileEnabled);
  }, []);

  useEffect(() => {
    setStoredChartPreferences({
      selectedSymbol,
      chartInterval,
      autoScale: autoScaleEnabled,
      logScale: logScaleEnabled,
      volumeProfileEnabled,
    });
  }, [selectedSymbol, chartInterval, autoScaleEnabled, logScaleEnabled, volumeProfileEnabled]);

  useEffect(() => {
    let mounted = true;
    async function loadSymbols(): Promise<void> {
      try {
        setLoading(true);
        const fetchedSymbols = await fetchSymbols();
        if (!mounted) {
          return;
        }
        setSymbols(fetchedSymbols);
        if (fetchedSymbols.length > 0) {
          setSelectedSymbol((current) => current || fetchedSymbols[0].symbol);
        }
      } catch (fetchError) {
        if (mounted) {
          const message = fetchError instanceof Error ? fetchError.message : "Unknown error";
          setError(message);
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }

    void loadSymbols();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedSymbol) {
      return;
    }
    if (candleSocketRef.current) {
      candleSocketRef.current.close();
      candleSocketRef.current = null;
    }
    setCandles([]);
    const ws = new WebSocket(getCandlesWebSocketUrl(selectedSymbol, chartInterval));
    candleSocketRef.current = ws;

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as
          | { event: "snapshot"; candles: Candle[] }
          | { event: "upsert"; candle: Candle }
          | { event: "heartbeat" };
        if (payload.event === "heartbeat") {
          return;
        }
        if (payload.event === "snapshot") {
          setCandles(payload.candles);
          return;
        }
        setCandles((current) => {
          if (current.length === 0) {
            return [payload.candle];
          }
          const last = current[current.length - 1];
          if (payload.candle.time > last.time) {
            return [...current, payload.candle];
          }
          if (payload.candle.time === last.time) {
            return [...current.slice(0, -1), payload.candle];
          }
          const idx = current.findIndex((c) => c.time === payload.candle.time);
          if (idx < 0) {
            return current;
          }
          const next = [...current];
          next[idx] = payload.candle;
          return next;
        });
      } catch {
        // ignore malformed payloads
      }
    };
    ws.onerror = () => {
      setError("Candles stream disconnected");
    };
    return () => {
      ws.close();
      candleSocketRef.current = null;
    };
  }, [selectedSymbol, chartInterval]);

  useEffect(() => {
    if (symbols.length === 0) {
      return;
    }

    let cancelled = false;
    async function loadTickers(): Promise<void> {
      try {
        const requested = symbols.slice(0, 100).map((item) => item.symbol);
        const snapshots = await fetchTickers(requested);
        if (cancelled) {
          return;
        }
        const bySymbol: Record<string, TickerSnapshot> = {};
        for (const snapshot of snapshots) {
          bySymbol[snapshot.symbol] = snapshot;
        }
        setTickers(bySymbol);
      } catch (fetchError) {
        if (!cancelled) {
          const message = fetchError instanceof Error ? fetchError.message : "Unknown error";
          setError(message);
        }
      }
    }

    void loadTickers();
    const timerId = window.setInterval(() => {
      void loadTickers();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(timerId);
    };
  }, [symbols]);

  useEffect(() => {
    if (!selectedSymbol) {
      return;
    }

    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }

    const ws = new WebSocket(getTicksWebSocketUrl(selectedSymbol));
    socketRef.current = ws;

    ws.onmessage = (event: MessageEvent<string>) => {
      const payload = JSON.parse(event.data) as TickerTick | { event: string };
      if ("event" in payload) {
        return;
      }
      setLatestTick(payload);
      setTickers((current) => ({
        ...current,
        [payload.symbol]: {
          symbol: payload.symbol,
          price: payload.price,
          change_24h_percent: payload.change_24h_percent,
          volume_24h: payload.volume_24h,
        },
      }));
    };

    ws.onerror = () => {
      setError("Realtime stream disconnected");
    };

    return () => {
      ws.close();
      socketRef.current = null;
    };
  }, [selectedSymbol]);

  const currentBar = useMemo<CurrentBar | null>(() => {
    if (candles.length === 0) {
      return null;
    }
    if (hoveredBarTime != null) {
      const bar = candles.find((c) => Math.floor(c.time / 1000) === hoveredBarTime);
      if (bar) {
        return {
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
          volume: bar.volume,
        };
      }
    }
    const last = candles[candles.length - 1];
    const close = latestTick ? latestTick.price : last.close;
    const high = latestTick ? Math.max(last.high, latestTick.price) : last.high;
    const low = latestTick ? Math.min(last.low, latestTick.price) : last.low;
    return {
      open: last.open,
      high,
      low,
      close,
      volume: last.volume,
    };
  }, [candles, latestTick, hoveredBarTime]);

  const value = useMemo<MarketDataContextValue>(
    () => ({
      symbols,
      selectedSymbol,
      setSelectedSymbol,
      chartInterval,
      setChartInterval,
      autoScaleEnabled,
      setAutoScaleEnabled,
      logScaleEnabled,
      setLogScaleEnabled,
      volumeProfileEnabled,
      setVolumeProfileEnabled,
      candles,
      currentBar,
      hoveredBarTime,
      setHoveredBarTime,
      tickers,
      latestTick,
      loading,
      error,
    }),
    [
      symbols,
      selectedSymbol,
      chartInterval,
      autoScaleEnabled,
      logScaleEnabled,
      volumeProfileEnabled,
      candles,
      currentBar,
      hoveredBarTime,
      tickers,
      latestTick,
      loading,
      error,
    ]
  );

  return <MarketDataContext.Provider value={value}>{children}</MarketDataContext.Provider>;
}

export function useMarketData(): MarketDataContextValue {
  const context = useContext(MarketDataContext);
  if (!context) {
    throw new Error("useMarketData must be used within MarketDataProvider");
  }
  return context;
}

