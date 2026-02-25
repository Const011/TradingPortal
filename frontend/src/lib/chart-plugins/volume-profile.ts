import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  AutoscaleInfo,
  Coordinate,
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  Logical,
  Time,
} from "lightweight-charts";
import { positionsBox } from "./helpers/positions";

interface VolumeProfileItem {
  y: Coordinate | null;
  width: number;
}

interface VolumeProfileRendererData {
  x: Coordinate | null;
  top: Coordinate | null;
  columnHeight: number;
  width: number;
  items: VolumeProfileItem[];
}

export interface VolumeProfileDataPoint {
  price: number;
  vol: number;
}

export interface VolumeProfileData {
  time: Time;
  profile: VolumeProfileDataPoint[];
  width: number;
}

class VolumeProfileRenderer implements IPrimitivePaneRenderer {
  private _data: VolumeProfileRendererData;

  constructor(data: VolumeProfileRendererData) {
    this._data = data;
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useBitmapCoordinateSpace((scope) => {
      if (this._data.x === null || this._data.top === null) return;
      const ctx = scope.context;
      const hRatio = scope.horizontalPixelRatio;
      const vRatio = scope.verticalPixelRatio;

      const width = 10*Math.max(...this._data.items.map((item) => item.width));

      // ctx.fillStyle = "rgba(0, 0, 255, 0.2)";
      // const bgHorizontal = positionsBox(
      //   this._data.x,
      //   this._data.x + this._data.width,
      //   hRatio
      // );
      // const bgVertical = positionsBox(
      //   this._data.top,
      //   this._data.top - this._data.columnHeight * this._data.items.length,
      //   vRatio
      // );
      // ctx.fillRect(
      //   bgHorizontal.position,
      //   bgVertical.position,
      //   bgHorizontal.length,
      //   bgVertical.length
      // );

      ctx.fillStyle = "rgba(80, 80, 255, 0.8)";
      const xAnchor = this._data.x + 25+width*hRatio;

      const pixelHeight = Math.max(
        1,
        Math.round(vRatio * this._data.columnHeight)
      );
      const aggregated = new Map<number, number>();
      for (const row of this._data.items) {
        if (row.y === null) continue;
        const pixelY = Math.round(vRatio * row.y);
        const existing = aggregated.get(pixelY) ?? 0;
        aggregated.set(pixelY, existing + row.width);
      }

      for (const [pixelY, width] of aggregated) {
        const itemHorizontal = positionsBox(xAnchor, xAnchor - 10*width*hRatio, hRatio );
        ctx.fillRect(
          itemHorizontal.position,
          pixelY,
          itemHorizontal.length,
          pixelHeight
        );
      }
    });
  }
}

class VolumeProfilePaneView implements IPrimitivePaneView {
  private _source: VolumeProfile;
  private _x: Coordinate | null = null;
  private _width = 6;
  private _columnHeight = 0;
  private _top: Coordinate | null = null;
  private _items: VolumeProfileItem[] = [];

  constructor(source: VolumeProfile) {
    this._source = source;
  }

  update(): void {
    const data = this._source._vpData;
    const series = this._source._series;
    const timeScale = this._source._chart.timeScale();
    this._x = timeScale.timeToCoordinate(data.time);
    this._width = timeScale.options().barSpacing * data.width;

    if (data.profile.length < 2) return;

    const y1 =
      series.priceToCoordinate(data.profile[0].price) ?? (0 as Coordinate);
    const y2 =
      series.priceToCoordinate(data.profile[1].price) ??
      (timeScale.height() as Coordinate);
    this._columnHeight = Math.max(1, Math.abs(y1 - y2));
    const maxVolume = data.profile.reduce(
      (acc, item) => Math.max(acc, item.vol),
      0
    );

    this._top = y1;

    this._items = data.profile.map((row) => ({
      y: series.priceToCoordinate(row.price),
      width: maxVolume > 0 ? (this._width * row.vol) / maxVolume : 0,
    }));
  }

  renderer(): VolumeProfileRenderer {
    return new VolumeProfileRenderer({
      x: this._x,
      top: this._top,
      columnHeight: this._columnHeight,
      width: this._width,
      items: this._items,
    });
  }
}

export class VolumeProfile implements ISeriesPrimitive<Time> {
  _chart: IChartApi;
  _series: ISeriesApi<"Candlestick">;
  _vpData: VolumeProfileData;
  _minPrice: number;
  _maxPrice: number;
  _paneViews: VolumeProfilePaneView[];

  constructor(
    chart: IChartApi,
    series: ISeriesApi<"Candlestick">,
    vpData: VolumeProfileData
  ) {
    this._chart = chart;
    this._series = series;
    this._vpData = vpData;
    this._minPrice = Infinity;
    this._maxPrice = -Infinity;
    this._vpData.profile.forEach((vpData) => {
      if (vpData.price < this._minPrice) this._minPrice = vpData.price;
      if (vpData.price > this._maxPrice) this._maxPrice = vpData.price;
    });
    this._paneViews = [new VolumeProfilePaneView(this)];
  }

  updateAllViews(): void {
    this._paneViews.forEach((pw) => pw.update());
  }

  autoscaleInfo(
    startTimePoint: Logical,
    endTimePoint: Logical
  ): AutoscaleInfo | null {
    const vpCoordinate = this._chart
      .timeScale()
      .timeToCoordinate(this._vpData.time);
    if (vpCoordinate === null) return null;
    const vpIndex = this._chart.timeScale().coordinateToLogical(vpCoordinate);
    if (vpIndex === null) return null;
    if (endTimePoint < vpIndex || startTimePoint > vpIndex + this._vpData.width)
      return null;
    return {
      priceRange: {
        minValue: this._minPrice,
        maxValue: this._maxPrice,
      },
    };
  }

  paneViews(): VolumeProfilePaneView[] {
    return this._paneViews;
  }
}
