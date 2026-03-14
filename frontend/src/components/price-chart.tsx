"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickData,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
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
import { toChartTimeLocal } from "@/lib/chart-time";
import { useMarketData } from "@/contexts/market-data-context";
import { OrderBlocks } from "@/lib/chart-plugins/order-blocks";
import { SupportResistanceLabelsPrimitive } from "@/lib/chart-plugins/support-resistance-labels";
import { StructurePrimitive } from "@/lib/chart-plugins/structure";
import { StrategySignalsPrimitive } from "@/lib/chart-plugins/strategy-signals";
import { VolumeProfile } from "@/lib/chart-plugins/volume-profile";
import {
  computeStrategyResults,
  tradeLogToStrategyResultsSummary,
} from "@/lib/strategy-results";
import { IndicatorControlPanel } from "@/components/indicator-control-panel";
import { StrategyResultsTable } from "@/components/strategy-results-table";

/** Convert UTC timestamp to local-adjusted seconds for chart (displays local time on axis). */
function toChartTime(msOrSec: number): Time {
  return toChartTimeLocal(msOrSec) as Time;
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
    structureEnabled,
    structure,
    candleColoringEnabled,
    strategyMarkersEnabled,
    strategySignals,
    tradeLogTrades,
    symbolAndIntervalLocked,
    gatewayConfig,
    cumulativeVolumeDelta,
    cumulativeVolumeDeltaEnabled,
  } = useMarketData();

  const orderBlocksForDisplay = useMemo(() => orderBlocks, [orderBlocks]);

  /** Order blocks with times converted to local for chart display. */
  const orderBlocksForChart = useMemo(() => {
    if (!orderBlocksForDisplay) return null;
    const convertOb = (ob: {
      startTime: number;
      endTime: number;
      breakerTime?: number | null;
      breakTime?: number | null;
      negatedTime?: number | null;
      top: number;
      bottom: number;
      breaker: boolean;
      fillColor: string;
    }) => ({
      ...ob,
      startTime: toChartTimeLocal(ob.startTime),
      endTime: toChartTimeLocal(ob.endTime),
      breakerTime:
        (ob.breakerTime ?? ob.breakTime) != null
          ? toChartTimeLocal((ob.breakerTime ?? ob.breakTime)!)
          : null,
      negatedTime:
        ob.negatedTime != null ? toChartTimeLocal(ob.negatedTime) : null,
    });
    return {
      ...orderBlocksForDisplay,
      bullish: (orderBlocksForDisplay.bullish ?? []).map(convertOb),
      bearish: (orderBlocksForDisplay.bearish ?? []).map(convertOb),
      bullishBreakers: (orderBlocksForDisplay.bullishBreakers ?? []).map(convertOb),
      bearishBreakers: (orderBlocksForDisplay.bearishBreakers ?? []).map(convertOb),
    };
  }, [orderBlocksForDisplay]);

  const structureForDisplay = useMemo(() => {
    if (!structure) return null;
    return structure;
  }, [structure]);

  /** Structure with times converted to local for chart display. */
  const structureForChart = useMemo(() => {
    if (!structureForDisplay) return null;
    const convertSeg = (seg: (typeof structureForDisplay.lines)[number]) => ({
      ...seg,
      from: { ...seg.from, time: toChartTimeLocal(seg.from.time) },
      to: { ...seg.to, time: toChartTimeLocal(seg.to.time) },
    });
    const convertLabel = (lbl: (typeof structureForDisplay.labels)[number]) => ({
      ...lbl,
      time: toChartTimeLocal(lbl.time),
    });
    return {
      ...structureForDisplay,
      lines: (structureForDisplay.lines ?? []).map(convertSeg),
      labels: (structureForDisplay.labels ?? []).map(convertLabel),
      swingLabels: (structureForDisplay.swingLabels ?? []).map(convertLabel),
      equalHighsLows: structureForDisplay.equalHighsLows
        ? {
            lines: (structureForDisplay.equalHighsLows.lines ?? []).map(convertSeg),
            labels: (structureForDisplay.equalHighsLows.labels ?? []).map(convertLabel),
          }
        : undefined,
    };
  }, [structureForDisplay]);

  const strategySignalsForDisplay = useMemo(() => {
    if (!strategySignals || !strategyMarkersEnabled) return null;
    return strategySignals;
  }, [strategySignals, strategyMarkersEnabled]);

  /** Strategy signals with times converted to local for chart display. */
  const strategySignalsForChart = useMemo(() => {
    if (!strategySignalsForDisplay) return null;
    return {
      ...strategySignalsForDisplay,
      markers: (strategySignalsForDisplay.markers ?? []).map((m) => ({
        ...m,
        time: toChartTimeLocal(m.time),
      })),
      stopLines: (strategySignalsForDisplay.stopLines ?? []).map((line) => ({
        ...line,
        from: { ...line.from, time: toChartTimeLocal(line.from.time) },
        to: { ...line.to, time: toChartTimeLocal(line.to.time) },
      })),
      targetLines: (strategySignalsForDisplay.targetLines ?? []).map((line) => ({
        ...line,
        from: { ...line.from, time: toChartTimeLocal(line.from.time) },
        to: { ...line.to, time: toChartTimeLocal(line.to.time) },
      })),
    };
  }, [strategySignalsForDisplay]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const cvdHistogramRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const cvdBuySeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const cvdSellSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const cvdStrengthSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const volumeProfilePrimitiveRef = useRef<VolumeProfile | null>(null);
  const supportResistancePriceLinesRef = useRef<IPriceLine[]>([]);
  const supportResistanceLabelsPrimitiveRef =
    useRef<SupportResistanceLabelsPrimitive | null>(null);
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

  // Adjust chart price precision dynamically based on the instrument's price
  // level so that small-value coins (e.g. DOGE, XRP) are not effectively
  // rounded to two decimals on the price scale.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || candles.length === 0) return;

    const last = candles[candles.length - 1];
    const refPrice = Math.abs(last.close || last.open || last.high || last.low || 0);
    if (!Number.isFinite(refPrice) || refPrice <= 0) return;

    let precision: number;
    if (refPrice >= 1000) {
      precision = 2;
    } else if (refPrice >= 1) {
      precision = 4;
    } else {
      precision = 6;
    }
    const minMove = 1 / Math.pow(10, precision);
    series.applyOptions({
      priceFormat: {
        type: "price",
        precision,
        minMove,
      },
    });
  }, [candles]);

  const strategyResults = useMemo(() => {
    if (gatewayConfig?.mode === "trade" && tradeLogTrades && tradeLogTrades.length > 0) {
      return tradeLogToStrategyResultsSummary(tradeLogTrades);
    }
    const signals = strategySignalsForDisplay ?? strategySignals;
    if (
      !strategyMarkersEnabled ||
      !signals?.events ||
      signals.events.length === 0 ||
      candles.length === 0
    ) {
      return null;
    }
    return computeStrategyResults(
      signals.events,
      candles,
      signals.stopSegments ?? []
    );
  }, [
    gatewayConfig?.mode,
    tradeLogTrades,
    strategyMarkersEnabled,
    strategySignalsForDisplay,
    strategySignals?.events,
    strategySignals?.stopSegments,
    candles,
  ]);

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

    // Cumulative volume delta indicator series.
    // We initially attach them to the main pane; a later effect moves them to
    // a dedicated indicator pane (pane index 1) only when enabled, and back to
    // pane 0 when disabled so the extra pane disappears.
    const cvdHistogram = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
    });
    const cvdBuySeries = chart.addSeries(LineSeries, {
      color: "#16a34a",
      lineWidth: 1,
    });
    const cvdSellSeries = chart.addSeries(LineSeries, {
      color: "#dc2626",
      lineWidth: 1,
    });
    const cvdStrengthSeries = chart.addSeries(LineSeries, {
      color: "#6b7280",
      lineWidth: 1,
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;
    volumeSeriesRef.current = volumeSeries;
    cvdHistogramRef.current = cvdHistogram;
    cvdBuySeriesRef.current = cvdBuySeries;
    cvdSellSeriesRef.current = cvdSellSeries;
    cvdStrengthSeriesRef.current = cvdStrengthSeries;

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
      const srLabelsPrim = supportResistanceLabelsPrimitiveRef.current;
      if (srLabelsPrim) {
        candlestickSeries.detachPrimitive(srLabelsPrim);
        supportResistanceLabelsPrimitiveRef.current = null;
      }
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
      cvdHistogramRef.current = null;
      cvdBuySeriesRef.current = null;
      cvdSellSeriesRef.current = null;
      cvdStrengthSeriesRef.current = null;
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
    const latestCandle = candles.length > 0 ? candles[candles.length - 1] : null;
    const barTimeSec = latestCandle
      ? (latestCandle.time >= 1e12 ? latestCandle.time / 1000 : latestCandle.time)
      : null;
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
  }, [latestTick, chartData, chartInterval, candles]);

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
      time: toChartTimeLocal(
        typeof volumeProfile.time === "number" ? volumeProfile.time : Number(volumeProfile.time)
      ) as Time,
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
    const histSeries = cvdHistogramRef.current;
    const buySeries = cvdBuySeriesRef.current;
    const sellSeries = cvdSellSeriesRef.current;
    const strengthSeries = cvdStrengthSeriesRef.current;
    if (!histSeries || !buySeries || !sellSeries || !strengthSeries) return;

    if (!cumulativeVolumeDeltaEnabled || !cumulativeVolumeDelta) {
      // Hide all CVD series, clear data, and move them back to pane 0 so the
      // extra pane (index 1) disappears when there are no visible series.
      histSeries.setData([]);
      buySeries.setData([]);
      sellSeries.setData([]);
      strengthSeries.setData([]);
      histSeries.applyOptions({ visible: false });
      buySeries.applyOptions({ visible: false });
      sellSeries.applyOptions({ visible: false });
      strengthSeries.applyOptions({ visible: false });
      histSeries.moveToPane(0);
      buySeries.moveToPane(0);
      sellSeries.moveToPane(0);
      strengthSeries.moveToPane(0);
      return;
    }

    const histData: HistogramData<Time>[] = cumulativeVolumeDelta.points.map((p) => {
      const isUp = p.delta >= 0;
      return {
        time: toChartTime(p.time),
        value: Math.abs(p.delta),
        color: isUp ? "#16a34a" : "#dc2626",
      };
    });
    const buyData = cumulativeVolumeDelta.points.map((p) => ({
      time: toChartTime(p.time),
      value: p.buy,
    }));
    const sellData = cumulativeVolumeDelta.points.map((p) => ({
      time: toChartTime(p.time),
      value: p.sell,
    }));
    const strengthData = cumulativeVolumeDelta.points.map((p) => ({
      time: toChartTime(p.time),
      value: p.strength,
    }));

    // Move all CVD series to pane 1 and make them visible when enabled.
    histSeries.moveToPane(1);
    buySeries.moveToPane(1);
    sellSeries.moveToPane(1);
    strengthSeries.moveToPane(1);

    // Ensure the CVD pane always uses a linear price scale, regardless of the
    // main chart's log/linear toggle.
    histSeries.priceScale().applyOptions({
      mode: PriceScaleMode.Normal,
      autoScale: true,
    });
    buySeries.priceScale().applyOptions({
      mode: PriceScaleMode.Normal,
      autoScale: true,
    });
    sellSeries.priceScale().applyOptions({
      mode: PriceScaleMode.Normal,
      autoScale: true,
    });
    strengthSeries.priceScale().applyOptions({
      mode: PriceScaleMode.Normal,
      autoScale: true,
    });

    histSeries.applyOptions({ visible: true });
    buySeries.applyOptions({ visible: true });
    sellSeries.applyOptions({ visible: true });
    strengthSeries.applyOptions({ visible: true });

    histSeries.setData(histData);
    buySeries.setData(buyData);
    sellSeries.setData(sellData);
    strengthSeries.setData(strengthData);
  }, [cumulativeVolumeDeltaEnabled, cumulativeVolumeDelta]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    // Remove existing S/R price lines
    for (const pl of supportResistancePriceLinesRef.current) {
      series.removePriceLine(pl);
    }
    supportResistancePriceLinesRef.current = [];
    const labelsPrimitive = supportResistanceLabelsPrimitiveRef.current;
    if (labelsPrimitive) {
      series.detachPrimitive(labelsPrimitive);
      supportResistanceLabelsPrimitiveRef.current = null;
    }

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

    const nextLabelsPrimitive = new SupportResistanceLabelsPrimitive(
      chart,
      series,
      supportResistance
    );
    series.attachPrimitive(nextLabelsPrimitive);
    supportResistanceLabelsPrimitiveRef.current = nextLabelsPrimitive;
  }, [supportResistanceEnabled, supportResistance]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (!orderBlocksEnabled || !orderBlocksForChart) {
      const primitive = orderBlocksPrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        orderBlocksPrimitiveRef.current = null;
      }
      return;
    }

    const hasBlocks =
      (orderBlocksForChart.bullish?.length ?? 0) > 0 ||
      (orderBlocksForChart.bearish?.length ?? 0) > 0 ||
      (orderBlocksForChart.bullishBreakers?.length ?? 0) > 0 ||
      (orderBlocksForChart.bearishBreakers?.length ?? 0) > 0;
    const primitive = orderBlocksPrimitiveRef.current;
    if (primitive) {
      series.detachPrimitive(primitive);
      orderBlocksPrimitiveRef.current = null;
    }
    if (hasBlocks) {
      const newPrimitive = new OrderBlocks(chart, series, orderBlocksForChart);
      series.attachPrimitive(newPrimitive);
      orderBlocksPrimitiveRef.current = newPrimitive;
    }
  }, [orderBlocksEnabled, orderBlocksForChart]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const markers =
      strategyMarkersEnabled &&
      strategySignalsForChart?.markers &&
      strategySignalsForChart.markers.length > 0
        ? strategySignalsForChart.markers.map((m) => ({
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
  }, [strategyMarkersEnabled, strategySignalsForChart?.markers]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    if (!structureEnabled || !structureForChart) {
      const primitive = structurePrimitiveRef.current;
      if (primitive) {
        series.detachPrimitive(primitive);
        structurePrimitiveRef.current = null;
      }
      return;
    }

    const hasStructure =
      (structureForChart.lines?.length ?? 0) > 0 ||
      (structureForChart.labels?.length ?? 0) > 0 ||
      (structureForChart.swingLabels?.length ?? 0) > 0 ||
      (structureForChart.equalHighsLows?.lines?.length ?? 0) > 0 ||
      (structureForChart.equalHighsLows?.labels?.length ?? 0) > 0;

    const primitive = structurePrimitiveRef.current;
    if (primitive && hasStructure) {
      primitive.updateData(structureForChart);
      return;
    }
    if (primitive) {
      series.detachPrimitive(primitive);
      structurePrimitiveRef.current = null;
    }
    if (hasStructure) {
      const newPrimitive = new StructurePrimitive(chart, series, structureForChart);
      series.attachPrimitive(newPrimitive);
      structurePrimitiveRef.current = newPrimitive;
    }
  }, [structureEnabled, structureForChart]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    const showStopLines =
      strategyMarkersEnabled &&
      strategySignalsForChart?.stopLines &&
      strategySignalsForChart.stopLines.length > 0;

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
    const newPrimitive = new StrategySignalsPrimitive(
      chart,
      series,
      strategySignalsForChart
    );
    series.attachPrimitive(newPrimitive);
    strategySignalsPrimitiveRef.current = newPrimitive;
  }, [strategyMarkersEnabled, strategySignalsForChart]);

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
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
            {symbolAndIntervalLocked && (
              <span style={{ fontSize: 12, opacity: 0.7, marginRight: 4 }}>
                Interval fixed
              </span>
            )}
            {CHART_INTERVAL_OPTIONS.map((option) => {
              const isActive = chartInterval === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={symbolAndIntervalLocked}
                  onClick={() => !symbolAndIntervalLocked && setChartInterval(option.value)}
                  style={{
                    ...(isActive ? intervalButtonActiveStyle : intervalButtonStyle),
                    cursor: symbolAndIntervalLocked ? "not-allowed" : "pointer",
                    opacity: symbolAndIntervalLocked ? 0.7 : 1,
                  }}
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
      <StrategyResultsTable summary={strategyResults} />
    </div>
  );
}

