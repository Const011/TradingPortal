/**
 * Browser storage for bookmarked tickers per market.
 * Shape: { [market: string]: string[] } e.g. { "spot": ["BTCUSDT"], "linear": ["BTCUSDT", "ETHUSDT"] }
 */

const STORAGE_KEY = "trading-portal-ticker-bookmarks";

export type TickerBookmarksByMarket = Record<string, string[]>;

const VALID_MARKETS = new Set(["spot", "linear"]);

function getMarketKey(market: string): string {
  return VALID_MARKETS.has(market) ? market : "spot";
}

function loadRaw(): TickerBookmarksByMarket {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed === null || typeof parsed !== "object") return {};
    const out: TickerBookmarksByMarket = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (VALID_MARKETS.has(key) && Array.isArray(value)) {
        const symbols = value.filter((s): s is string => typeof s === "string");
        if (symbols.length > 0) out[key] = symbols;
      }
    }
    return out;
  } catch {
    return {};
  }
}

export function getBookmarkedTickers(market: string): string[] {
  const key = getMarketKey(market);
  const data = loadRaw();
  return data[key] ?? [];
}

export function isTickerBookmarked(market: string, symbol: string): boolean {
  const list = getBookmarkedTickers(market);
  return list.includes(symbol);
}

export function addTickerBookmark(market: string, symbol: string): void {
  if (typeof window === "undefined") return;
  const key = getMarketKey(market);
  const data = loadRaw();
  const list = data[key] ?? [];
  if (list.includes(symbol)) return;
  data[key] = [...list, symbol];
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

export function removeTickerBookmark(market: string, symbol: string): void {
  if (typeof window === "undefined") return;
  const key = getMarketKey(market);
  const data = loadRaw();
  const list = data[key] ?? [];
  const next = list.filter((s) => s !== symbol);
  if (next.length === 0) {
    delete data[key];
  } else {
    data[key] = next;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

export function toggleTickerBookmark(market: string, symbol: string): boolean {
  const bookmarked = isTickerBookmarked(market, symbol);
  if (bookmarked) {
    removeTickerBookmark(market, symbol);
    return false;
  }
  addTickerBookmark(market, symbol);
  return true;
}
