import {
  CHART_INTERVAL_OPTIONS,
  DEFAULT_CHART_INTERVAL,
  type ChartIntervalValue,
} from "@/lib/constants/chart-intervals";

const STORAGE_KEY = "trading-portal-chart-preferences";

const VALID_INTERVALS = new Set<string>(
  CHART_INTERVAL_OPTIONS.map((o) => o.value)
);

export const VOLUME_PROFILE_WINDOW_DEFAULT = 2000;

export type StoredChartPreferences = {
  selectedSymbol: string;
  chartInterval: ChartIntervalValue;
  autoScale: boolean;
  logScale: boolean;
  volumeProfileEnabled: boolean;
  volumeProfileWindow: number;
  supportResistanceEnabled: boolean;
  orderBlocksEnabled: boolean;
  structureEnabled: boolean;
};

const DEFAULTS: StoredChartPreferences = {
  selectedSymbol: "",
  chartInterval: DEFAULT_CHART_INTERVAL,
  autoScale: true,
  logScale: false,
  volumeProfileEnabled: false,
  volumeProfileWindow: VOLUME_PROFILE_WINDOW_DEFAULT,
  supportResistanceEnabled: false,
  orderBlocksEnabled: false,
  structureEnabled: false,
};

function parseStored(raw: string | null): Partial<StoredChartPreferences> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Partial<StoredChartPreferences> = {};
    if (typeof parsed.selectedSymbol === "string") {
      out.selectedSymbol = parsed.selectedSymbol;
    }
    if (
      typeof parsed.chartInterval === "string" &&
      VALID_INTERVALS.has(parsed.chartInterval)
    ) {
      out.chartInterval = parsed.chartInterval as ChartIntervalValue;
    }
    if (typeof parsed.autoScale === "boolean") {
      out.autoScale = parsed.autoScale;
    }
    if (typeof parsed.logScale === "boolean") {
      out.logScale = parsed.logScale;
    }
    if (typeof parsed.volumeProfileEnabled === "boolean") {
      out.volumeProfileEnabled = parsed.volumeProfileEnabled;
    }
    if (
      typeof parsed.volumeProfileWindow === "number" &&
      parsed.volumeProfileWindow >= 100 &&
      parsed.volumeProfileWindow <= 10000
    ) {
      out.volumeProfileWindow = parsed.volumeProfileWindow;
    }
    if (typeof parsed.supportResistanceEnabled === "boolean") {
      out.supportResistanceEnabled = parsed.supportResistanceEnabled;
    }
    if (typeof parsed.orderBlocksEnabled === "boolean") {
      out.orderBlocksEnabled = parsed.orderBlocksEnabled;
    }
    if (typeof parsed.structureEnabled === "boolean") {
      out.structureEnabled = parsed.structureEnabled;
    }
    return out;
  } catch {
    return {};
  }
}

export function getStoredChartPreferences(): StoredChartPreferences {
  if (typeof window === "undefined") {
    return DEFAULTS;
  }
  const raw = window.localStorage.getItem(STORAGE_KEY);
  const partial = parseStored(raw);
  return {
    selectedSymbol:
      partial.selectedSymbol !== undefined ? partial.selectedSymbol : DEFAULTS.selectedSymbol,
    chartInterval:
      partial.chartInterval !== undefined ? partial.chartInterval : DEFAULTS.chartInterval,
    autoScale: partial.autoScale !== undefined ? partial.autoScale : DEFAULTS.autoScale,
    logScale: partial.logScale !== undefined ? partial.logScale : DEFAULTS.logScale,
    volumeProfileEnabled:
      partial.volumeProfileEnabled !== undefined
        ? partial.volumeProfileEnabled
        : DEFAULTS.volumeProfileEnabled,
    volumeProfileWindow:
      partial.volumeProfileWindow !== undefined
        ? partial.volumeProfileWindow
        : DEFAULTS.volumeProfileWindow,
    supportResistanceEnabled:
      partial.supportResistanceEnabled !== undefined
        ? partial.supportResistanceEnabled
        : DEFAULTS.supportResistanceEnabled,
    orderBlocksEnabled:
      partial.orderBlocksEnabled !== undefined
        ? partial.orderBlocksEnabled
        : DEFAULTS.orderBlocksEnabled,
    structureEnabled:
      partial.structureEnabled !== undefined
        ? partial.structureEnabled
        : DEFAULTS.structureEnabled,
  };
}

export function setStoredChartPreferences(
  prefs: Partial<StoredChartPreferences>
): void {
  if (typeof window === "undefined") return;
  const current = getStoredChartPreferences();
  const next: StoredChartPreferences = {
    selectedSymbol: prefs.selectedSymbol ?? current.selectedSymbol,
    chartInterval: prefs.chartInterval ?? current.chartInterval,
    autoScale: prefs.autoScale ?? current.autoScale,
    logScale: prefs.logScale ?? current.logScale,
    volumeProfileEnabled:
      prefs.volumeProfileEnabled ?? current.volumeProfileEnabled,
    volumeProfileWindow:
      prefs.volumeProfileWindow ?? current.volumeProfileWindow,
    supportResistanceEnabled:
      prefs.supportResistanceEnabled ?? current.supportResistanceEnabled,
    orderBlocksEnabled:
      prefs.orderBlocksEnabled ?? current.orderBlocksEnabled,
    structureEnabled:
      prefs.structureEnabled ?? current.structureEnabled,
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}
