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
import { fetchCandles, fetchSymbols, fetchTickers, getTicksWebSocketUrl } from "@/lib/api/market";
import { Candle, SymbolInfo, TickerSnapshot, TickerTick } from "@/lib/types/market";

type MarketDataContextValue = {
  symbols: SymbolInfo[];
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
  chartInterval: ChartIntervalValue;
  setChartInterval: (interval: ChartIntervalValue) => void;
  candles: Candle[];
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
  const [candles, setCandles] = useState<Candle[]>([]);
  const [tickers, setTickers] = useState<Record<string, TickerSnapshot>>({});
  const [latestTick, setLatestTick] = useState<TickerTick | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);

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

  const value = useMemo<MarketDataContextValue>(
    () => ({
      symbols,
      selectedSymbol,
      setSelectedSymbol,
      chartInterval,
      setChartInterval,
      candles,
      tickers,
      latestTick,
      loading,
      error,
    }),
    [symbols, selectedSymbol, chartInterval, candles, tickers, latestTick, loading, error]
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

