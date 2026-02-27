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
import type { SmartMoneyStructureData } from "@/lib/types/market";

class StructureRenderer implements IPrimitivePaneRenderer {
  constructor(
    private _lines: { x1: number; y1: number; x2: number; y2: number; color: string; style: string }[],
    private _labels: { x: number; y: number; text: string; color: string }[]
  ) {}

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      for (const line of this._lines) {
        ctx.strokeStyle = line.color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash(
          line.style === "dashed" ? [4, 4] : line.style === "dotted" ? [2, 2] : []
        );
        ctx.beginPath();
        ctx.moveTo(line.x1, line.y1);
        ctx.lineTo(line.x2, line.y2);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const label of this._labels) {
        ctx.font = "11px sans-serif";
        ctx.fillStyle = label.color;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label.text, label.x, label.y);
      }
    });
  }
}

class StructurePaneView implements IPrimitivePaneView {
  constructor(private _source: StructurePrimitive) {}

  renderer(): StructureRenderer {
    const { chart, series, data } = this._source;
    const timeScale = chart.timeScale();
    const lines: { x1: number; y1: number; x2: number; y2: number; color: string; style: string }[] = [];
    const labels: { x: number; y: number; text: string; color: string }[] = [];

    const toCoord = (t: number, p: number): { x: number | null; y: number | null } => {
      const x = timeScale.timeToCoordinate(t as Time);
      const y = series.priceToCoordinate(p);
      return { x: x ?? null, y: y ?? null };
    };

    const allLines = [
      ...(data.lines ?? []),
      ...(data.equalHighsLows?.lines ?? []),
    ];
    const allLabels = [
      ...(data.labels ?? []),
      ...(data.swingLabels ?? []),
      ...(data.equalHighsLows?.labels ?? []),
    ];
    for (const seg of allLines) {
      const from = toCoord(seg.from.time, seg.from.price);
      const to = toCoord(seg.to.time, seg.to.price);
      if (from.x != null && from.y != null && to.x != null && to.y != null) {
        lines.push({
          x1: from.x,
          y1: from.y,
          x2: to.x,
          y2: to.y,
          color: seg.color,
          style: seg.style ?? "solid",
        });
      }
    }
    for (const lbl of allLabels) {
      const pt = toCoord(lbl.time, lbl.price);
      if (pt.x != null && pt.y != null) {
        labels.push({ x: pt.x, y: pt.y, text: lbl.text, color: lbl.color });
      }
    }
    return new StructureRenderer(lines, labels);
  }
}

export class StructurePrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: SmartMoneyStructureData;
  private _paneViews: StructurePaneView[];
  private _unsubscribe: (() => void) | null = null;

  constructor(
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    data: SmartMoneyStructureData
  ) {
    this.chart = chart;
    this.series = series;
    this.data = data;
    this._paneViews = [new StructurePaneView(this)];
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

  paneViews(): StructurePaneView[] {
    return this._paneViews;
  }
}
