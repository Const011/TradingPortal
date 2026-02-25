"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickData,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  IChartApi,
  ISeriesApi,
  Time,
  PriceScaleMode,
  type HistogramData,
} from "lightweight-charts";

import { CHART_INTERVAL_OPTIONS } from "@/lib/constants/chart-intervals";
import { useMarketData } from "@/contexts/market-data-context";

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
  const { candles, latestTick, selectedSymbol, chartInterval, setChartInterval } = useMarketData();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [autoScaleEnabled, setAutoScaleEnabled] = useState<boolean>(true);
  const [logScaleEnabled, setLogScaleEnabled] = useState<boolean>(false);

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
      height: 520,
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

    const observer = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) {
        return;
      }
      chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

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
    chartRef.current?.timeScale().fitContent();
  }, [chartData, volumeData, selectedSymbol]);

  useEffect(() => {
    if (!seriesRef.current || !latestTick) {
      return;
    }

    const latestBar = chartData.length > 0 ? chartData[chartData.length - 1] : null;
    const time = latestBar ? latestBar.time : toChartTime(latestTick.ts);
    const close = latestTick.price;
    const open = latestBar ? latestBar.open : close;
    const high = latestBar ? Math.max(latestBar.high, close) : close;
    const low = latestBar ? Math.min(latestBar.low, close) : close;
    seriesRef.current.update({ time, open, high, low, close });
  }, [latestTick, chartData]);

  return (
    <div style={{ width: "100%", minWidth: 400 }}>
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
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => setAutoScaleEnabled((current) => !current)}
            style={autoScaleEnabled ? intervalButtonActiveStyle : intervalButtonStyle}
          >
            Auto
          </button>
          <button
            type="button"
            onClick={() => setLogScaleEnabled((current) => !current)}
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

