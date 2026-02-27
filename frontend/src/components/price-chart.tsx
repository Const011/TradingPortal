"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickData,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
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
  background: "#111a2b",
  color: "#d6dfeb",
};
const intervalButtonActiveStyle = {
  ...intervalButtonStyle,
  background: "#1f3b65",
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
  } = useMarketData();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const volumeProfilePrimitiveRef = useRef<VolumeProfile | null>(null);
  const supportResistancePriceLinesRef = useRef<IPriceLine[]>([]);
  const lastFittedKeyRef = useRef<string | null>(null);

  const chartData = useMemo<CandlestickData<Time>[]>(() => {
    return candles.map((item) => ({
      time: toChartTime(item.time),
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }));
  }, [candles]);

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
        background: { type: ColorType.Solid, color: "#0c111d" },
        textColor: "#d6dfeb",
      },
      grid: {
        vertLines: { color: "#1a2538" },
        horzLines: { color: "#1a2538" },
      },
      width: containerRef.current.clientWidth,
      height: 800,
      rightPriceScale: {
        borderColor: "#253349",
      },
      timeScale: {
        borderColor: "#253349",
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
        color: "rgba(190, 185, 245, 0.42)", // line.color ??
        lineWidth: Math.min(4, Math.max(1, Math.round(line.width))) as 1 | 2 | 3 | 4,
        lineStyle: styleToLineStyle[line.style ?? "solid"] ?? LineStyle.Solid,
        axisLabelVisible: false,
      });
      supportResistancePriceLinesRef.current.push(priceLine);
    }
  }, [supportResistanceEnabled, supportResistance]);

  return (
    <div
      style={{ width: "100%", minWidth: 400 }}
      onMouseLeave={() => setHoveredBarTime(null)}
    >
      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 12,
          alignItems: "center",
          flexWrap: "wrap",
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
          <IndicatorControlPanel />
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
      <div ref={containerRef} style={{ width: "100%" }} />
    </div>
  );
}

