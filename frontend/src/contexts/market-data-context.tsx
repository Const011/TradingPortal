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
  CHART_INTERVAL_OPTIONS,
  DEFAULT_CHART_INTERVAL,
  type ChartIntervalValue,
} from "@/lib/constants/chart-intervals";
import { toChartTimeLocal } from "@/lib/chart-time";
import {
  getStoredChartPreferences,
  setStoredChartPreferences,
  STRATEGY_MARKERS_WINDOW_DEFAULT,
  VOLUME_PROFILE_WINDOW_DEFAULT,
} from "@/lib/chart-preferences-storage";
import {
  fetchSymbols,
  fetchTickers,
  fetchTradeLog,
  getCandlesWebSocketUrl,
  getTicksWebSocketUrl,
  type TradeLogTrade,
} from "@/lib/api/market";
import { useGateway } from "@/contexts/gateway-context";
import {
  Candle,
  type CurrentBar,
  SymbolInfo,
  TickerSnapshot,
  TickerTick,
  type VolumeProfileData,
  type SupportResistanceData,
  type OrderBlocksData,
  type SmartMoneyStructureData,
  type StrategySignalsData,
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
  volumeProfileWindow: number;
  setVolumeProfileWindow: (window: number) => void;
  supportResistanceEnabled: boolean;
  setSupportResistanceEnabled: (enabled: boolean) => void;
  orderBlocksEnabled: boolean;
  setOrderBlocksEnabled: (enabled: boolean) => void;
  structureEnabled: boolean;
  setStructureEnabled: (enabled: boolean) => void;
  candleColoringEnabled: boolean;
  setCandleColoringEnabled: (enabled: boolean) => void;
  strategyMarkersEnabled: boolean;
  setStrategyMarkersEnabled: (enabled: boolean) => void;
  strategyMarkersWindow: number;
  setStrategyMarkersWindow: (window: number) => void;
  obShowBull: number;
  setObShowBull: (n: number) => void;
  obShowBear: number;
  setObShowBear: (n: number) => void;
  swingLabelsShow: number;
  setSwingLabelsShow: (n: number) => void;
  candles: Candle[];
  volumeProfile: VolumeProfileData | null;
  supportResistance: SupportResistanceData | null;
  orderBlocks: OrderBlocksData | null;
  structure: SmartMoneyStructureData | null;
  strategySignals: StrategySignalsData | null;
  /** When mode=trading: trades from trade-log API for results table. */
  tradeLogTrades: TradeLogTrade[] | null;
  /** When mode=trading: symbol and interval are fixed by gateway config; controls disabled. */
  symbolAndIntervalLocked: boolean;
  /** Gateway config from GET /api/v1/mode (mode, symbol, interval, bars_window). */
  gatewayConfig: { mode: "simulation" | "trade"; symbol?: string; interval?: string; bars_window?: number } | null;
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
  const { backendBaseUrl, gatewayConfig } = useGateway();
  const isTrading = gatewayConfig?.mode === "trade";

  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>("");
  const [chartInterval, setChartInterval] = useState<ChartIntervalValue>(DEFAULT_CHART_INTERVAL);
  const [autoScaleEnabled, setAutoScaleEnabled] = useState<boolean>(true);
  const [logScaleEnabled, setLogScaleEnabled] = useState<boolean>(false);
  const [volumeProfileEnabled, setVolumeProfileEnabled] = useState<boolean>(false);
  const [volumeProfileWindow, setVolumeProfileWindow] = useState<number>(VOLUME_PROFILE_WINDOW_DEFAULT);
  const [supportResistanceEnabled, setSupportResistanceEnabled] = useState<boolean>(false);
  const [orderBlocksEnabled, setOrderBlocksEnabled] = useState<boolean>(false);
  const [structureEnabled, setStructureEnabled] = useState<boolean>(false);
  const [candleColoringEnabled, setCandleColoringEnabled] = useState<boolean>(false);
  const [strategyMarkersEnabled, setStrategyMarkersEnabled] = useState<boolean>(false);
  const [strategyMarkersWindow, setStrategyMarkersWindow] = useState<number>(STRATEGY_MARKERS_WINDOW_DEFAULT);
  const [obShowBull, setObShowBull] = useState<number>(5);
  const [obShowBear, setObShowBear] = useState<number>(5);
  const [swingLabelsShow, setSwingLabelsShow] = useState<number>(15);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [volumeProfile, setVolumeProfile] = useState<VolumeProfileData | null>(null);
  const [supportResistance, setSupportResistance] = useState<SupportResistanceData | null>(null);
  const [orderBlocks, setOrderBlocks] = useState<OrderBlocksData | null>(null);
  const [structure, setStructure] = useState<SmartMoneyStructureData | null>(null);
  const [strategySignals, setStrategySignals] = useState<StrategySignalsData | null>(null);
  const [tradeLogTrades, setTradeLogTrades] = useState<TradeLogTrade[] | null>(null);
  const [tickers, setTickers] = useState<Record<string, TickerSnapshot>>({});
  const [latestTick, setLatestTick] = useState<TickerTick | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredBarTime, setHoveredBarTime] = useState<number | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const candleSocketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const prefs = getStoredChartPreferences();
    if (!isTrading) {
      setSelectedSymbol(prefs.selectedSymbol);
      setChartInterval(prefs.chartInterval);
    }
    setAutoScaleEnabled(prefs.autoScale);
    setLogScaleEnabled(prefs.logScale);
    setVolumeProfileEnabled(prefs.volumeProfileEnabled);
    setVolumeProfileWindow(prefs.volumeProfileWindow);
    setSupportResistanceEnabled(prefs.supportResistanceEnabled);
    setOrderBlocksEnabled(prefs.orderBlocksEnabled);
    setStructureEnabled(prefs.structureEnabled);
    setCandleColoringEnabled(prefs.candleColoringEnabled);
    setStrategyMarkersEnabled(prefs.strategyMarkersEnabled);
    setStrategyMarkersWindow(prefs.strategyMarkersWindow);
    setObShowBull(prefs.obShowBull);
    setObShowBear(prefs.obShowBear);
    setSwingLabelsShow(prefs.swingLabelsShow);
  }, []);

  // When user selects a new ticker, re-enable auto scale so the chart rescales like pressing "Auto".
  useEffect(() => {
    if (!selectedSymbol) return;
    setAutoScaleEnabled(true);
  }, [selectedSymbol]);

  useEffect(() => {
    if (isTrading && gatewayConfig) {
      const symbol = gatewayConfig.symbol ?? "BTCUSDT";
      const interval = gatewayConfig.interval ?? "60";
      setSelectedSymbol(symbol);
      const validInterval = CHART_INTERVAL_OPTIONS.some((o) => o.value === interval)
        ? (interval as ChartIntervalValue)
        : DEFAULT_CHART_INTERVAL;
      setChartInterval(validInterval);
    }
  }, [isTrading, gatewayConfig?.symbol, gatewayConfig?.interval]);

  useEffect(() => {
    setStoredChartPreferences({
      selectedSymbol,
      chartInterval,
      autoScale: autoScaleEnabled,
      logScale: logScaleEnabled,
      volumeProfileEnabled,
      volumeProfileWindow,
      supportResistanceEnabled,
      orderBlocksEnabled,
      structureEnabled,
      candleColoringEnabled,
      strategyMarkersEnabled,
      strategyMarkersWindow,
      obShowBull,
      obShowBear,
      swingLabelsShow,
    });
  }, [selectedSymbol, chartInterval, autoScaleEnabled, logScaleEnabled, volumeProfileEnabled, volumeProfileWindow, supportResistanceEnabled, orderBlocksEnabled, structureEnabled, candleColoringEnabled, strategyMarkersEnabled, strategyMarkersWindow, obShowBull, obShowBear, swingLabelsShow]);

  useEffect(() => {
    if (!backendBaseUrl) {
      setSymbols([]);
      setTickers({});
      setLoading(false);
      return;
    }
    let mounted = true;
    async function loadSymbols(): Promise<void> {
      try {
        setLoading(true);
        if (isTrading && gatewayConfig?.symbol) {
          const tradingSymbol = gatewayConfig.symbol;
          setSymbols([
            { symbol: tradingSymbol, baseCoin: "", quoteCoin: "", status: "Trading" },
          ]);
          try {
            const snapshots = await fetchTickers(backendBaseUrl, [tradingSymbol]);
            if (!mounted) return;
            const bySymbol: Record<string, TickerSnapshot> = {};
            for (const snapshot of snapshots) {
              bySymbol[snapshot.symbol] = snapshot;
            }
            setTickers(bySymbol);
          } catch {
            if (mounted) setError("Failed to fetch tickers");
          }
        } else {
          const fetchedSymbols = await fetchSymbols(backendBaseUrl);
          if (!mounted) return;
          setSymbols(fetchedSymbols);
          if (fetchedSymbols.length > 0) {
            setSelectedSymbol((current) => current || fetchedSymbols[0].symbol);
          }
          if (fetchedSymbols.length > 0) {
            const requested = fetchedSymbols.slice(0, 100).map((item) => item.symbol);
            try {
              const snapshots = await fetchTickers(backendBaseUrl, requested);
              if (!mounted) return;
              const bySymbol: Record<string, TickerSnapshot> = {};
              for (const snapshot of snapshots) {
                bySymbol[snapshot.symbol] = snapshot;
              }
              setTickers(bySymbol);
            } catch {
              if (mounted) setError("Failed to fetch tickers");
            }
          }
        }
      } catch (fetchError) {
        if (mounted) {
          const message = fetchError instanceof Error ? fetchError.message : "Unknown error";
          setError(message);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void loadSymbols();
    return () => {
      mounted = false;
    };
  }, [backendBaseUrl, isTrading, gatewayConfig?.symbol]);

  useEffect(() => {
    if (!backendBaseUrl || !selectedSymbol) {
      return;
    }
    if (candleSocketRef.current) {
      candleSocketRef.current.close();
      candleSocketRef.current = null;
    }
    setCandles([]);
    setVolumeProfile(null);
    setSupportResistance(null);
    setOrderBlocks(null);
    setStructure(null);
    setStrategySignals(null);
    setTradeLogTrades(null);
    const effectiveStrategyMarkers: "off" | "simulation" | "trade" =
      !strategyMarkersEnabled ? "off" : isTrading ? "trade" : "simulation";
    const vpWindow =
      isTrading && gatewayConfig?.bars_window != null
        ? gatewayConfig.bars_window
        : volumeProfileWindow;
    const ws = new WebSocket(
      getCandlesWebSocketUrl(
        backendBaseUrl,
        selectedSymbol,
        chartInterval,
        vpWindow,
        effectiveStrategyMarkers
      )
    );
    candleSocketRef.current = ws;

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as
          | { event: "snapshot"; candles: Candle[]; graphics?: { volumeProfile?: VolumeProfileData; supportResistance?: SupportResistanceData; orderBlocks?: OrderBlocksData; smartMoney?: { structure?: SmartMoneyStructureData }; strategySignals?: StrategySignalsData }; volumeProfile?: VolumeProfileData }
          | { event: "heartbeat" };
        if (payload.event === "heartbeat") {
          return;
        }
        if (payload.event === "snapshot") {
          const graphics = payload.graphics ?? (payload.volumeProfile ? { volumeProfile: payload.volumeProfile } : undefined);
          setCandles(payload.candles);
          setVolumeProfile(graphics?.volumeProfile ?? null);
          setSupportResistance(graphics?.supportResistance ?? null);
          setOrderBlocks(graphics?.orderBlocks ?? null);
          setStructure(graphics?.smartMoney?.structure ?? null);
          // Simulation: markers from stream. Trading: markers from trade log only (never from stream).
          if (!isTrading) {
            setStrategySignals(graphics?.strategySignals ?? null);
          }
        }
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
  }, [
    backendBaseUrl,
    selectedSymbol,
    chartInterval,
    volumeProfileWindow,
    strategyMarkersEnabled,
    isTrading,
    gatewayConfig?.bars_window,
  ]);

  useEffect(() => {
    if (!isTrading || !backendBaseUrl || !selectedSymbol) {
      return;
    }
    let cancelled = false;
    async function loadTradeLog(): Promise<void> {
      try {
        const { trades } = await fetchTradeLog(
          backendBaseUrl,
          selectedSymbol,
          chartInterval
        );
        if (cancelled) return;
        setTradeLogTrades(trades);
        const merged = {
          markers: trades.flatMap((t) => t.markers ?? []),
          stopLines: trades.flatMap((t) => t.stopLines ?? []),
          events: trades.flatMap((t) => t.events ?? []),
          stopSegments: trades.flatMap((t) => t.stopSegments ?? []),
        } as StrategySignalsData;
        setStrategySignals(
          merged.markers?.length || merged.events?.length
            ? merged
            : null
        );
      } catch {
        if (!cancelled) setTradeLogTrades([]);
      }
    }
    void loadTradeLog();
    const intervalId = window.setInterval(loadTradeLog, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [backendBaseUrl, selectedSymbol, chartInterval, isTrading]);

  useEffect(() => {
    if (!backendBaseUrl || symbols.length === 0) {
      return;
    }

    let cancelled = false;
    async function loadTickers(): Promise<void> {
      try {
        const requested = symbols.slice(0, 100).map((item) => item.symbol);
        const snapshots = await fetchTickers(backendBaseUrl, requested);
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
  }, [backendBaseUrl, symbols]);

  useEffect(() => {
    if (!backendBaseUrl || !selectedSymbol) {
      return;
    }

    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }

    const ws = new WebSocket(getTicksWebSocketUrl(backendBaseUrl, selectedSymbol));
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
  }, [backendBaseUrl, selectedSymbol]);

  const currentBar = useMemo<CurrentBar | null>(() => {
    if (candles.length === 0) {
      return null;
    }
    if (hoveredBarTime != null) {
      const bar = candles.find(
        (c) => toChartTimeLocal(c.time) === hoveredBarTime
      );
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
      volumeProfileWindow,
      setVolumeProfileWindow,
      supportResistanceEnabled,
      setSupportResistanceEnabled,
  orderBlocksEnabled,
  setOrderBlocksEnabled,
  structureEnabled,
  setStructureEnabled,
      candleColoringEnabled,
      setCandleColoringEnabled,
      strategyMarkersEnabled,
      setStrategyMarkersEnabled,
      strategyMarkersWindow,
      setStrategyMarkersWindow,
      obShowBull,
      setObShowBull,
      obShowBear,
      setObShowBear,
      swingLabelsShow,
      setSwingLabelsShow,
      candles,
      volumeProfile,
      supportResistance,
      orderBlocks,
      structure,
      strategySignals,
      tradeLogTrades,
      symbolAndIntervalLocked: isTrading,
      gatewayConfig,
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
      volumeProfileWindow,
      supportResistanceEnabled,
      orderBlocksEnabled,
      structureEnabled,
      candleColoringEnabled,
      strategyMarkersEnabled,
      strategyMarkersWindow,
      obShowBull,
      obShowBear,
      swingLabelsShow,
      candles,
      volumeProfile,
      supportResistance,
      orderBlocks,
      structure,
      strategySignals,
      tradeLogTrades,
      gatewayConfig,
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

