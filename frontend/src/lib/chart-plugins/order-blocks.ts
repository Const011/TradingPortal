import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  LogicalRange,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { OrderBlockData, OrderBlocksData } from "@/lib/types/market";

function formatStrengthCompact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";

  const formatWithDigits = (v: number): string => {
    if (v < 10) {
      return v.toFixed(2);
    }
    if (v < 100) {
      return v.toFixed(1);
    }
    // For 3+ digits, avoid decimals to keep label compact (e.g. 154k).
    return Math.floor(v).toString();
  };

  if (abs >= 1_000_000) {
    return `${sign}${formatWithDigits(abs / 1_000_000)}M`;
  }
  if (abs >= 1_000) {
    return `${sign}${formatWithDigits(abs / 1_000)}k`;
  }
  return `${sign}${formatWithDigits(abs)}`;
}

interface BoxToDraw {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  fillColor: string;
  strengthText?: string;
}

class OrderBlocksRenderer implements IPrimitivePaneRenderer {
  private _boxes: BoxToDraw[] = [];

  constructor(boxes: BoxToDraw[]) {
    this._boxes = boxes;
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      for (const box of this._boxes) {
        ctx.fillStyle = box.fillColor;
        const x = Math.min(box.x1, box.x2);
        const y = Math.min(box.y1, box.y2);
        const w = Math.abs(box.x2 - box.x1);
        const h = Math.abs(box.y2 - box.y1);
        ctx.fillRect(x, y, w, h);

        if (box.strengthText) {
          ctx.font = "11px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
          ctx.fillStyle = "#4b5563"; // neutral dark gray
          ctx.textBaseline = "middle";
          const textX = Math.max(box.x1, box.x2) + 4;
          const textY = y + h / 2;
          ctx.fillText(box.strengthText, textX, textY);
        }
      }
    });
  }
}

class OrderBlocksPaneView implements IPrimitivePaneView {
  private _source: OrderBlocks;

  constructor(source: OrderBlocks) {
    this._source = source;
  }

  renderer(): OrderBlocksRenderer {
    const { chart, series, data } = this._source;
    const timeScale = chart.timeScale();
    const boxes: BoxToDraw[] = [];

    const visibleRange = timeScale.getVisibleLogicalRange();
    if (visibleRange === null) return new OrderBlocksRenderer([]);

    function addBox(
      t1: number,
      p1: number,
      t2: number,
      p2: number,
      fillColor: string,
      strengthText?: string
    ): void {
      const x1 = timeScale.timeToCoordinate(t1 as Time);
      const x2 = timeScale.timeToCoordinate(t2 as Time);
      const y1 = series.priceToCoordinate(p1);
      const y2 = series.priceToCoordinate(p2);
      if (x1 !== null && x2 !== null && y1 !== null && y2 !== null) {
        boxes.push({
          x1,
          y1,
          x2,
          y2,
          fillColor,
          strengthText,
        });
      }
    }

    function drawOrderBlocks(blocks: OrderBlockData[]): void {
      for (const ob of blocks) {
        const tStart = ob.startTime;
        const tEnd = ob.endTime;
        const top = ob.top;
        const bottom = ob.bottom;
        const breakerT = ob.breakerTime ?? ob.breakTime;
        const negatedT = ob.negatedTime ?? null;
        const strength =
          typeof ob.strengthIndex === "number" && Number.isFinite(ob.strengthIndex)
            ? ob.strengthIndex
            : undefined;
        const strengthText =
          strength !== undefined && strength > 0
            ? formatStrengthCompact(strength)
            : undefined;

        // If the block has been negated, clamp its visual end at negatedT.
        const lastTime = negatedT != null ? negatedT : tEnd;

        if (ob.breaker && breakerT != null) {
          // Breaker case: we may have two time segments (pre-break and post-break).
          // - If negated: show strength only on the negation segment (breakerT -> negatedT).
          // - If not negated: show strength only on the final segment (breakerT -> lastTime).

          // 1) Before breaker: never show strength text here.
          addBox(tStart, top, breakerT, bottom, ob.fillColor, undefined);

          // 2) From breaker until negation (or end if never negated).
          if (lastTime > breakerT) {
            addBox(
              breakerT,
              top,
              lastTime,
              bottom,
              ob.fillColor,
              strengthText
            );
          }
        } else {
          // No breaker: draw from start until negation/end, show strength once at the end.
          addBox(tStart, top, lastTime, bottom, ob.fillColor, strengthText);
        }
      }
    }

    drawOrderBlocks(data.bullish ?? []);
    drawOrderBlocks(data.bearish ?? []);
    drawOrderBlocks(data.bullishBreakers ?? []);
    drawOrderBlocks(data.bearishBreakers ?? []);

    return new OrderBlocksRenderer(boxes);
  }
}

export class OrderBlocks implements ISeriesPrimitive<Time> {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  data: OrderBlocksData;
  private _paneViews: OrderBlocksPaneView[];
  private _unsubscribeVisibleRange: (() => void) | null = null;

  constructor(
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    data: OrderBlocksData
  ) {
    this.chart = chart;
    this.series = series;
    this.data = data;
    this._paneViews = [new OrderBlocksPaneView(this)];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    const handler = (_range: LogicalRange | null) => {
      param.requestUpdate();
    };
    param.chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    this._unsubscribeVisibleRange = () =>
      param.chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
  }

  detached(): void {
    this._unsubscribeVisibleRange?.();
    this._unsubscribeVisibleRange = null;
  }

  updateAllViews(): void {
    // Coordinates computed in renderer at draw time
  }

  paneViews(): OrderBlocksPaneView[] {
    return this._paneViews;
  }
}
