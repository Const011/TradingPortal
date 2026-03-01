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
import {
  getStoredChartPreferences,
  setStoredChartPreferences,
  VOLUME_PROFILE_WINDOW_DEFAULT,
} from "@/lib/chart-preferences-storage";
import {
  backendMode,
  fetchGatewayConfig,
  fetchSymbols,
  fetchTickers,
  fetchTradeLog,
  getCandlesWebSocketUrl,
  getTicksWebSocketUrl,
  type TradeLogTrade,
} from "@/lib/api/market";
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
  strategyMarkers: "off" | "simulation" | "trade";
  setStrategyMarkers: (mode: "off" | "simulation" | "trade") => void;
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
  const [volumeProfileWindow, setVolumeProfileWindow] = useState<number>(VOLUME_PROFILE_WINDOW_DEFAULT);
  const [supportResistanceEnabled, setSupportResistanceEnabled] = useState<boolean>(false);
  const [orderBlocksEnabled, setOrderBlocksEnabled] = useState<boolean>(false);
  const [structureEnabled, setStructureEnabled] = useState<boolean>(false);
  const [candleColoringEnabled, setCandleColoringEnabled] = useState<boolean>(false);
  const [strategyMarkers, setStrategyMarkers] = useState<"off" | "simulation" | "trade">("off");
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
    if (backendMode !== "trading") {
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
    setStrategyMarkers(prefs.strategyMarkers);
    setObShowBull(prefs.obShowBull);
    setObShowBear(prefs.obShowBear);
    setSwingLabelsShow(prefs.swingLabelsShow);
  }, []);

  useEffect(() => {
    if (backendMode !== "trading") return;
    let mounted = true;
    async function loadGatewayConfig(): Promise<void> {
      try {
        const config = await fetchGatewayConfig();
        if (!mounted) return;
        const symbol = config.symbol ?? "BTCUSDT";
        const interval = config.interval ?? "60";
        setSelectedSymbol(symbol);
        const validInterval = CHART_INTERVAL_OPTIONS.some((o) => o.value === interval)
          ? (interval as ChartIntervalValue)
          : DEFAULT_CHART_INTERVAL;
        setChartInterval(validInterval);
      } catch {
        if (mounted) {
          setSelectedSymbol("BTCUSDT");
          setChartInterval(DEFAULT_CHART_INTERVAL);
        }
      }
    }
    void loadGatewayConfig();
    return () => {
      mounted = false;
    };
  }, []);

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
      strategyMarkers,
      obShowBull,
      obShowBear,
      swingLabelsShow,
    });
  }, [selectedSymbol, chartInterval, autoScaleEnabled, logScaleEnabled, volumeProfileEnabled, volumeProfileWindow, supportResistanceEnabled, orderBlocksEnabled, structureEnabled, candleColoringEnabled, strategyMarkers, obShowBull, obShowBear, swingLabelsShow]);

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
        if (fetchedSymbols.length > 0 && backendMode !== "trading") {
          setSelectedSymbol((current) => current || fetchedSymbols[0].symbol);
        }
        // Load tickers immediately when symbols are available (avoids effect timing / Strict Mode issues)
        if (fetchedSymbols.length > 0) {
          const requested = fetchedSymbols.slice(0, 100).map((item) => item.symbol);
          try {
            const snapshots = await fetchTickers(requested);
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
    setVolumeProfile(null);
    setSupportResistance(null);
    setOrderBlocks(null);
    setStructure(null);
    setStrategySignals(null);
    setTradeLogTrades(null);
    const ws = new WebSocket(
      getCandlesWebSocketUrl(
        selectedSymbol,
        chartInterval,
        volumeProfileWindow,
        strategyMarkers
      )
    );
    candleSocketRef.current = ws;

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as
          | { event: "snapshot"; candles: Candle[]; graphics?: { volumeProfile?: VolumeProfileData; supportResistance?: SupportResistanceData; orderBlocks?: OrderBlocksData; smartMoney?: { structure?: SmartMoneyStructureData }; strategySignals?: StrategySignalsData }; volumeProfile?: VolumeProfileData }
          | { event: "upsert"; candle: Candle; graphics?: { volumeProfile?: VolumeProfileData; supportResistance?: SupportResistanceData; orderBlocks?: OrderBlocksData; smartMoney?: { structure?: SmartMoneyStructureData }; strategySignals?: StrategySignalsData }; volumeProfile?: VolumeProfileData }
          | { event: "heartbeat" };
        if (payload.event === "heartbeat") {
          return;
        }
        const graphics = payload.graphics ?? (payload.volumeProfile ? { volumeProfile: payload.volumeProfile } : undefined);
        if (payload.event === "snapshot") {
          setCandles(payload.candles);
          setVolumeProfile(graphics?.volumeProfile ?? null);
          setSupportResistance(graphics?.supportResistance ?? null);
          setOrderBlocks(graphics?.orderBlocks ?? null);
          setStructure(graphics?.smartMoney?.structure ?? null);
          if (backendMode !== "trading") {
            setStrategySignals(graphics?.strategySignals ?? null);
          }
          return;
        }
        if (graphics) {
          if (graphics.volumeProfile !== undefined) setVolumeProfile(graphics.volumeProfile ?? null);
          if (graphics.supportResistance !== undefined) setSupportResistance(graphics.supportResistance ?? null);
          if (graphics.orderBlocks !== undefined) setOrderBlocks(graphics.orderBlocks ?? null);
          if (graphics.smartMoney?.structure !== undefined) setStructure(graphics.smartMoney.structure ?? null);
          if (backendMode !== "trading" && graphics.strategySignals !== undefined) {
            setStrategySignals(graphics.strategySignals ?? null);
          }
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
  }, [selectedSymbol, chartInterval, volumeProfileWindow, strategyMarkers]);

  useEffect(() => {
    if (backendMode !== "trading" || !selectedSymbol) {
      return;
    }
    let cancelled = false;
    async function loadTradeLog(): Promise<void> {
      try {
        const { trades } = await fetchTradeLog(selectedSymbol, chartInterval);
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
      strategyMarkers,
      setStrategyMarkers,
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
      symbolAndIntervalLocked: backendMode === "trading",
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
      strategyMarkers,
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

