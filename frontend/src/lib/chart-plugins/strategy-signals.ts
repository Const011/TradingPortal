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
import type { StrategySignalsData } from "@/lib/types/market";

class StopLinesRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _lines: {
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      color: string;
      width: number;
    }[]
  ) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      for (const line of this._lines) {
        ctx.strokeStyle = line.color;
        ctx.lineWidth = line.width;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(line.x1, line.y1);
        ctx.lineTo(line.x2, line.y2);
        ctx.stroke();
      }
      ctx.setLineDash([]);
    });
  }
}

class StopLinesPaneView implements IPrimitivePaneView {
  constructor(private _source: StrategySignalsPrimitive) {}

  renderer(): StopLinesRenderer {
    const { chart, series, data } = this._source;
    const timeScale = chart.timeScale();
    const lines: {
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      color: string;
      width: number;
    }[] = [];

    const toCoord = (t: number, p: number): { x: number | null; y: number | null } => {
      const x = timeScale.timeToCoordinate(t as Time);
      const y = series.priceToCoordinate(p);
      return { x: x ?? null, y: y ?? null };
    };

    for (const seg of data.stopLines ?? []) {
      const from = toCoord(seg.from.time, seg.from.price);
      const to = toCoord(seg.to.time, seg.to.price);
      if (from.x != null && from.y != null && to.x != null && to.y != null) {
        lines.push({
          x1: from.x,
          y1: from.y,
          x2: to.x,
          y2: to.y,
          color: seg.color ?? "#f59e0b",
          width: seg.width ?? 2,
        });
      }
    }
    return new StopLinesRenderer(lines);
  }
}

export class StrategySignalsPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: StrategySignalsData;
  private _paneViews: StopLinesPaneView[];
  private _unsubscribe: (() => void) | null = null;

  constructor(
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    data: StrategySignalsData
  ) {
    this.chart = chart;
    this.series = series;
    this.data = data;
    this._paneViews = [new StopLinesPaneView(this)];
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

  paneViews(): StopLinesPaneView[] {
    return this._paneViews;
  }
}
