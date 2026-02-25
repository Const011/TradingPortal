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
  fetchCandles,
  fetchSymbols,
  fetchTickers,
  getBarUpdatesWebSocketUrl,
  getTicksWebSocketUrl,
} from "@/lib/api/market";
import {
  type BarUpdate,
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
  candles: Candle[];
  /** Real-time kline update for current bar (accumulated volume); null when not available. */
  liveBarUpdate: BarUpdate | null;
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
  const [candles, setCandles] = useState<Candle[]>([]);
  const [tickers, setTickers] = useState<Record<string, TickerSnapshot>>({});
  const [latestTick, setLatestTick] = useState<TickerTick | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredBarTime, setHoveredBarTime] = useState<number | null>(null);
  const [liveBarUpdate, setLiveBarUpdate] = useState<BarUpdate | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const barSocketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const prefs = getStoredChartPreferences();
    setSelectedSymbol(prefs.selectedSymbol);
    setChartInterval(prefs.chartInterval);
    setAutoScaleEnabled(prefs.autoScale);
    setLogScaleEnabled(prefs.logScale);
  }, []);

  useEffect(() => {
    setStoredChartPreferences({
      selectedSymbol,
      chartInterval,
      autoScale: autoScaleEnabled,
      logScale: logScaleEnabled,
    });
  }, [selectedSymbol, chartInterval, autoScaleEnabled, logScaleEnabled]);

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

    let mounted = true;
    async function loadCandles(): Promise<void> {
      try {
        setError(null);
        const fetchedCandles = await fetchCandles(selectedSymbol, chartInterval);
        if (mounted) {
          setCandles(fetchedCandles);
        }
      } catch (fetchError) {
        if (mounted) {
          const message = fetchError instanceof Error ? fetchError.message : "Unknown error";
          setError(message);
        }
      }
    }

    void loadCandles();
    return () => {
      mounted = false;
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

  useEffect(() => {
    if (!selectedSymbol) {
      return;
    }
    if (barSocketRef.current) {
      barSocketRef.current.close();
      barSocketRef.current = null;
    }
    setLiveBarUpdate(null);
    const barWs = new WebSocket(
      getBarUpdatesWebSocketUrl(selectedSymbol, chartInterval)
    );
    barSocketRef.current = barWs;
    barWs.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as BarUpdate;
        setLiveBarUpdate(payload);
      } catch {
        // ignore
      }
    };
    return () => {
      barWs.close();
      barSocketRef.current = null;
    };
  }, [selectedSymbol, chartInterval]);

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
    const lastStartMs = last.time >= 1e12 ? last.time : last.time * 1000;
    const liveStartMs = liveBarUpdate
      ? liveBarUpdate.start >= 1e12
        ? liveBarUpdate.start
        : liveBarUpdate.start * 1000
      : 0;
    const isSameBar = liveBarUpdate != null && liveStartMs === lastStartMs;

    if (isSameBar) {
      return {
        open: liveBarUpdate.open,
        high: liveBarUpdate.high,
        low: liveBarUpdate.low,
        close: liveBarUpdate.close,
        volume: liveBarUpdate.volume,
      };
    }
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
  }, [candles, latestTick, hoveredBarTime, liveBarUpdate]);

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
      candles,
      liveBarUpdate,
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
      candles,
      liveBarUpdate,
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

