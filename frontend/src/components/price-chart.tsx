"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickData,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  Time,
  PriceScaleMode,
  type HistogramData,
} from "lightweight-charts";

import {
  CHART_INTERVAL_OPTIONS,
  chartIntervalSeconds,
} from "@/lib/constants/chart-intervals";
import { useMarketData } from "@/contexts/market-data-context";
import { OrderBlocks } from "@/lib/chart-plugins/order-blocks";
import { StructurePrimitive } from "@/lib/chart-plugins/structure";
import { StrategySignalsPrimitive } from "@/lib/chart-plugins/strategy-signals";
import { VolumeProfile } from "@/lib/chart-plugins/volume-profile";
import { IndicatorControlPanel } from "@/components/indicator-control-panel";

function toChartTime(milliseconds: number): Time {
  return Math.floor(milliseconds / 1000) as Time;
}

const intervalButtonStyle = {
  padding: "8px 16px",
  fontSize: 14,
  borderWidth: 1,
  borderStyle: "solid",
  borderColor: "#2a3b54",
  borderRadius: 6,
  cursor: "pointer" as const,
  background: "#ffffff",
  color: "#000000",
};
const intervalButtonActiveStyle = {
  ...intervalButtonStyle,
  background: "#e8f0fe",
  borderColor: "#3b82f6",
};

export function PriceChart() {
  const {
    candles,
    latestTick,
    selectedSymbol,
    chartInterval,
    setChartInterval,
    setHoveredBarTime,
    autoScaleEnabled,
    setAutoScaleEnabled,
    logScaleEnabled,
    setLogScaleEnabled,
    volumeProfileEnabled,
    volumeProfile,
    supportResistanceEnabled,
    supportResistance,
    orderBlocksEnabled,
    orderBlocks,
    obShowBull,
    obShowBear,
    structureEnabled,
    structure,
    swingLabelsShow,
    candleColoringEnabled,
    strategyMarkers,
    strategySignals,
  } = useMarketData();

  const orderBlocksForDisplay = useMemo(() => {
    if (!orderBlocks) return null;
    const sliceBull = obShowBull > 0 ? obShowBull : 999;
    const sliceBear = obShowBear > 0 ? obShowBear : 999;
    return {
      ...orderBlocks,
      bullish: (orderBlocks.bullish ?? []).slice(0, sliceBull),
      bearish: (orderBlocks.bearish ?? []).slice(0, sliceBear),
      bullishBreakers: (orderBlocks.bullishBreakers ?? []).slice(0, sliceBull),
      bearishBreakers: (orderBlocks.bearishBreakers ?? []).slice(0, sliceBear),
    };
  }, [orderBlocks, obShowBull, obShowBear]);

  const structureForDisplay = useMemo(() => {
    if (!structure) return null;
    const limit = swingLabelsShow > 0 ? swingLabelsShow : 999;
    return {
      ...structure,
      swingLabels: (structure.swingLabels ?? []).slice(-limit),
    };
  }, [structure, swingLabelsShow]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const volumeProfilePrimitiveRef = useRef<VolumeProfile | null>(null);
  const supportResistancePriceLinesRef = useRef<IPriceLine[]>([]);
  const orderBlocksPrimitiveRef = useRef<OrderBlocks | null>(null);
  const structurePrimitiveRef = useRef<StructurePrimitive | null>(null);
  const strategySignalsPrimitiveRef = useRef<StrategySignalsPrimitive | null>(null);
  const seriesMarkersRef = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null);
  const lastFittedKeyRef = useRef<string | null>(null);

  const chartData = useMemo<CandlestickData<Time>[]>(() => {
    const candleColors = structure?.candleColors;
    const applyColors = candleColoringEnabled && candleColors && Object.keys(candleColors).length > 0;

    return candles.map((item) => {
      const point: CandlestickData<Time> = {
        time: toChartTime(item.time),
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
      };
      if (applyColors) {
        const c = candleColors[item.time];
        if (c) {
          point.color = c;
          point.wickColor = c;
          point.borderColor = c;
        }
      }
      return point;
    });
  }, [candles, structure?.candleColors, candleColoringEnabled]);

  const volumeData = useMemo<HistogramData<Time>[]>(() => {
    return candles.map((item, index, all) => {
      const previousClose = index > 0 ? all[index - 1].close : item.open;
      const isUp = item.close >= previousClose;
      return {
        time: toChartTime(item.time),
        value: item.volume,
        color: isUp ? "#2ecc71" : "#e74c3c",
      };
    });
  }, [candles]);

  useEffect(() => {
    if (!containerRef.current || chartRef.current) {
      return;
    }

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#000000",
      },
      grid: {
        vertLines: { color: "#e0e0e0" },
        horzLines: { color: "#e0e0e0" },
      },
      width: containerRef.current.clientWidth,
      height: 800,
      rightPriceScale: {
        borderColor: "#cccccc",
      },
      timeScale: {
        borderColor: "#cccccc",
        timeVisible: true,
      },
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#2ecc71",
      downColor: "#e74c3c",
      borderVisible: false,
      wickUpColor: "#2ecc71",
      wickDownColor: "#e74c3c",
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.7,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;
    volumeSeriesRef.current = volumeSeries;

    chart.subscribeCrosshairMove((param) => {
      if (param.time != null) {
        setHoveredBarTime(typeof param.time === "number" ? param.time : Number(param.time));
      }
    });

    const observer = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) {
        return;
      }
      chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      for (const pl of supportResistancePriceLinesRef.current) {
        candlestickSeries.removePriceLine(pl);
      }
      supportResistancePriceLinesRef.current = [];
      const obPrim = orderBlocksPrimitiveRef.current;
      if (obPrim) {
        candlestickSeries.detachPrimitive(obPrim);
        orderBlocksPrimitiveRef.current = null;
      }
      const structPrim = structurePrimitiveRef.current;
      if (structPrim) {
        candlestickSeries.detachPrimitive(structPrim);
        structurePrimitiveRef.current = null;
      }
      const strategyPrim = strategySignalsPrimitiveRef.current;
      if (strategyPrim) {
        candlestickSeries.detachPrimitive(strategyPrim);
        strategySignalsPrimitiveRef.current = null;
      }
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volumeSeriesRef.current = null;
      volumeProfilePrimitiveRef.current = null;
    };
  }, [setHoveredBarTime]);

  useEffect(() => {
    if (!seriesRef.current) {
      return;
    }
    const mode = logScaleEnabled ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal;
    seriesRef.current.priceScale().applyOptions({
      autoScale: autoScaleEnabled,
      mode,
    });
  }, [autoScaleEnabled, logScaleEnabled]);

  useEffect(() => {
    if (seriesRef.current) {
      seriesRef.current.setData(chartData);
    }
    if (volumeSeriesRef.current) {
      volumeSeriesRef.current.setData(volumeData);
    }
    const symbolIntervalKey = `${selectedSymbol}:${chartInterval}`;
    if (
      chartData.length > 0 &&
      lastFittedKeyRef.current !== symbolIntervalKey
    ) {
      chartRef.current?.timeScale().fitContent();
      lastFittedKeyRef.current = symbolIntervalKey;
    }
  }, [chartData, volumeData, selectedSymbol, chartInterval]);

  useEffect(() => {
    if (!seriesRef.current || !latestTick) {
      return;
    }

    const latestBar = chartData.length > 0 ? chartData[chartData.length - 1] : null;
    const barTimeSec =
      typeof latestBar?.time === "number" ? latestBar.time : null;
    const intervalSec = chartIntervalSeconds(chartInterval);
    const tickTimeSec = latestTick.ts / 1000;
    if (
      barTimeSec != null &&
      (tickTimeSec < barTimeSec || tickTimeSec >= barTimeSec + intervalSec)
    ) {
      return;
    }
    const time = latestBar ? latestBar.time : toChartTime(latestTick.ts);
    const close = latestTick.price;
    const open = latestBar ? latestBar.open : close;
    const high = latestBar ? Math.max(latestBar.high, close) : close;
    const low = latestBar ? Math.min(latestBar.low, close) : close;
    seriesRef.current.update({ time, open, high, low, close });
  }, [latestTick, chartData, chartInterval]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (!volumeProfileEnabled || !volumeProfile) {
      const primitive = volumeProfilePrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        volumeProfilePrimitiveRef.current = null;
      }
      return;
    }

    const vpData = {
      ...volumeProfile,
      time: volumeProfile.time as Time,
    };
    const primitive = volumeProfilePrimitiveRef.current;
    if (primitive) {
      series.detachPrimitive(primitive);
    }
    const newPrimitive = new VolumeProfile(chart, series, vpData);
    series.attachPrimitive(newPrimitive);
    volumeProfilePrimitiveRef.current = newPrimitive;
  }, [volumeProfileEnabled, volumeProfile]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Remove existing S/R price lines
    for (const pl of supportResistancePriceLinesRef.current) {
      series.removePriceLine(pl);
    }
    supportResistancePriceLinesRef.current = [];

    if (
      !supportResistanceEnabled ||
      !supportResistance ||
      supportResistance.lines.length === 0
    ) {
      return;
    }

    const styleToLineStyle: Record<string, LineStyle> = {
      solid: LineStyle.Solid,
      dotted: LineStyle.Dotted,
      dashed: LineStyle.Dashed,
    };

    for (const line of supportResistance.lines) {
      if (line.type !== "horizontalLine") continue;
      const priceLine = series.createPriceLine({
        price: line.price,
        color: line.color ?? "rgba(51, 33, 243, 0.24)",
        lineWidth: Math.min(4, Math.max(1, Math.round(line.width))) as 1 | 2 | 3 | 4,
        lineStyle: styleToLineStyle[line.style ?? "solid"] ?? LineStyle.Solid,
        axisLabelVisible: false,
      });
      supportResistancePriceLinesRef.current.push(priceLine);
    }
  }, [supportResistanceEnabled, supportResistance]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (!orderBlocksEnabled || !orderBlocksForDisplay) {
      const primitive = orderBlocksPrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        orderBlocksPrimitiveRef.current = null;
      }
      return;
    }

    const hasBlocks =
      (orderBlocksForDisplay.bullish?.length ?? 0) > 0 ||
      (orderBlocksForDisplay.bearish?.length ?? 0) > 0 ||
      (orderBlocksForDisplay.bullishBreakers?.length ?? 0) > 0 ||
      (orderBlocksForDisplay.bearishBreakers?.length ?? 0) > 0;
    const primitive = orderBlocksPrimitiveRef.current;
    if (primitive) {
      series.detachPrimitive(primitive);
      orderBlocksPrimitiveRef.current = null;
    }
    if (hasBlocks) {
      const newPrimitive = new OrderBlocks(chart, series, orderBlocksForDisplay);
      series.attachPrimitive(newPrimitive);
      orderBlocksPrimitiveRef.current = newPrimitive;
    }
  }, [orderBlocksEnabled, orderBlocksForDisplay]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const markers =
      strategyMarkers !== "off" &&
      strategySignals?.markers &&
      strategySignals.markers.length > 0
        ? strategySignals.markers.map((m) => ({
            time: m.time as Time,
            position: (m.position === "below" ? "belowBar" : "aboveBar") as
              | "belowBar"
              | "aboveBar",
            shape: (m.shape === "arrowUp"
              ? "arrowUp"
              : m.shape === "arrowDown"
                ? "arrowDown"
                : "circle") as "arrowUp" | "arrowDown" | "circle",
            color: m.color,
          }))
        : [];

    if (seriesMarkersRef.current) {
      seriesMarkersRef.current.setMarkers(markers);
    } else if (markers.length > 0) {
      seriesMarkersRef.current = createSeriesMarkers(series, markers);
    }
    if (markers.length === 0 && seriesMarkersRef.current) {
      seriesMarkersRef.current.setMarkers([]);
    }
  }, [strategyMarkers, strategySignals?.markers]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (!structureEnabled || !structureForDisplay) {
      const primitive = structurePrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        structurePrimitiveRef.current = null;
      }
      return;
    }

    const hasStructure =
      (structureForDisplay.lines?.length ?? 0) > 0 ||
      (structureForDisplay.labels?.length ?? 0) > 0 ||
      (structureForDisplay.swingLabels?.length ?? 0) > 0 ||
      (structureForDisplay.equalHighsLows?.lines?.length ?? 0) > 0 ||
      (structureForDisplay.equalHighsLows?.labels?.length ?? 0) > 0;
    const primitive = structurePrimitiveRef.current;
    if (primitive) {
      series.detachPrimitive(primitive);
      structurePrimitiveRef.current = null;
    }
    if (hasStructure) {
      const newPrimitive = new StructurePrimitive(chart, series, structureForDisplay);
      series.attachPrimitive(newPrimitive);
      structurePrimitiveRef.current = newPrimitive;
    }
  }, [structureEnabled, structureForDisplay]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    const showStopLines =
      strategyMarkers !== "off" &&
      strategySignals?.stopLines &&
      strategySignals.stopLines.length > 0;

    if (!showStopLines) {
      const primitive = strategySignalsPrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        strategySignalsPrimitiveRef.current = null;
      }
      return;
    }

    const primitive = strategySignalsPrimitiveRef.current;
    if (primitive) {
      series.detachPrimitive(primitive);
    }
    const newPrimitive = new StrategySignalsPrimitive(chart, series, strategySignals);
    series.attachPrimitive(newPrimitive);
    strategySignalsPrimitiveRef.current = newPrimitive;
  }, [strategyMarkers, strategySignals]);

  return (
    <div
      style={{ width: "100%", minWidth: 400 }}
      onMouseLeave={() => setHoveredBarTime(null)}
    >
      <div style={{ marginBottom: 12 }}>
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: 8,
          }}
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {CHART_INTERVAL_OPTIONS.map((option) => {
              const isActive = chartInterval === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setChartInterval(option.value)}
                  style={isActive ? intervalButtonActiveStyle : intervalButtonStyle}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => setAutoScaleEnabled(!autoScaleEnabled)}
              style={autoScaleEnabled ? intervalButtonActiveStyle : intervalButtonStyle}
            >
              Auto
            </button>
            <button
              type="button"
              onClick={() => setLogScaleEnabled(!logScaleEnabled)}
              style={logScaleEnabled ? intervalButtonActiveStyle : intervalButtonStyle}
            >
              Log
            </button>
          </div>
        </div>
        <IndicatorControlPanel />
      </div>
      <div ref={containerRef} style={{ width: "100%" }} />
    </div>
  );
}

