import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { SupportResistanceData } from "@/lib/types/market";

function formatLineWidth(value: number): string {
  return value.toFixed(3);
}

class SupportResistanceLabelsRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _labels: { x: number; y: number; text: string; color: string }[]
  ) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      ctx.font = "11px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.fillStyle = "#4b5563";
      ctx.textBaseline = "middle";
      ctx.textAlign = "left";

      for (const label of this._labels) {
        ctx.fillStyle = label.color;
        ctx.fillText(label.text, label.x, label.y);
      }
    });
  }
}

class SupportResistanceLabelsPaneView implements IPrimitivePaneView {
  constructor(private _source: SupportResistanceLabelsPrimitive) {}

  renderer(): SupportResistanceLabelsRenderer {
    const { chart, series, data } = this._source;
    const timeScale = chart.timeScale();
    const visibleRange = timeScale.getVisibleLogicalRange();
    if (visibleRange === null) {
      return new SupportResistanceLabelsRenderer([]);
    }

    const rightEdgeX = timeScale.logicalToCoordinate(visibleRange.to);
    if (rightEdgeX === null) {
      return new SupportResistanceLabelsRenderer([]);
    }

    const labels: { x: number; y: number; text: string; color: string }[] = [];

    for (const line of data.lines) {
      if (line.type !== "horizontalLine") {
        continue;
      }
      const y = series.priceToCoordinate(line.price);
      if (y === null) {
        continue;
      }
      labels.push({
        x: rightEdgeX + 4,
        y,
        text: formatLineWidth(line.width),
        color: "#4b5563",
      });
    }

    return new SupportResistanceLabelsRenderer(labels);
  }
}

export class SupportResistanceLabelsPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: SupportResistanceData;
  private _paneViews: SupportResistanceLabelsPaneView[];
  private _unsubscribe: (() => void) | null = null;

  constructor(
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    data: SupportResistanceData
  ) {
    this.chart = chart;
    this.series = series;
    this.data = data;
    this._paneViews = [new SupportResistanceLabelsPaneView(this)];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    const handler = () => param.requestUpdate();
    param.chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    this._unsubscribe = () =>
      param.chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
  }

  detached(): void {
    this._unsubscribe?.();
    this._unsubscribe = null;
  }

  updateAllViews(): void {}

  paneViews(): SupportResistanceLabelsPaneView[] {
    return this._paneViews;
  }
}
