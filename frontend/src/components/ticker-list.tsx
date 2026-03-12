"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useGateway } from "@/contexts/gateway-context";
import { useMarketData } from "@/contexts/market-data-context";
import {
  getBookmarkedTickers,
  isTickerBookmarked,
  toggleTickerBookmark,
} from "@/lib/ticker-bookmarks-storage";

function formatPrice(price: number): string {
  const abs = Math.abs(price);
  if (abs >= 1000) return price.toFixed(2);
  if (abs >= 1) return price.toFixed(4);
  return price.toFixed(6);
}

const DEFAULT_TICKER_PANEL_WIDTH = 280;

export type TickerListProps = {
  /** Panel width in px; used when embedded in a resizable layout. */
  width?: number;
};

export function TickerList({ width = DEFAULT_TICKER_PANEL_WIDTH }: TickerListProps) {
  const { gatewayConfig } = useGateway();
  const market = gatewayConfig?.market ?? "spot";
  const { symbols, selectedSymbol, setSelectedSymbol, tickers, symbolAndIntervalLocked } =
    useMarketData();

  const [bookmarkedList, setBookmarkedList] = useState<string[]>(() =>
    getBookmarkedTickers(market)
  );

  useEffect(() => {
    setBookmarkedList(getBookmarkedTickers(market));
  }, [market]);

  const handleToggleBookmark = useCallback(
    (e: React.MouseEvent, symbol: string) => {
      e.preventDefault();
      e.stopPropagation();
      toggleTickerBookmark(market, symbol);
      setBookmarkedList(getBookmarkedTickers(market));
    },
    [market]
  );

  const sortedSymbols = useMemo(() => {
    const bookmarkedSet = new Set(bookmarkedList);
    const bookmarked: typeof symbols = [];
    const rest: typeof symbols = [];
    for (const item of symbols) {
      if (bookmarkedSet.has(item.symbol)) bookmarked.push(item);
      else rest.push(item);
    }
    const order = new Map(bookmarkedList.map((s, i) => [s, i]));
    bookmarked.sort((a, b) => (order.get(a.symbol) ?? 0) - (order.get(b.symbol) ?? 0));
    return [...bookmarked, ...rest];
  }, [symbols, bookmarkedList]);

  return (
    <aside
      style={{
        width,
        borderRight: "1px solid #e0e0e0",
        padding: 12,
        overflowY: "auto",
      }}
    >
      <h3 style={{ marginTop: 0 }}>Tickers – {market}</h3>
      {symbolAndIntervalLocked && (
        <p style={{ margin: "4px 0 8px", fontSize: 12, opacity: 0.7 }}>
          Fixed by gateway config
        </p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {sortedSymbols.map((item) => {
          const active = item.symbol === selectedSymbol;
          const snapshot = tickers[item.symbol];
          const change = snapshot ? snapshot.change_24h_percent : null;
          const changeColor =
            change === null ? "#9bb0d1" : change >= 0 ? "#2ecc71" : "#e74c3c";
          const bookmarked = isTickerBookmarked(market, item.symbol);
          return (
            <div
              key={item.symbol}
              role="button"
              tabIndex={0}
              onClick={() => !symbolAndIntervalLocked && setSelectedSymbol(item.symbol)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  if (!symbolAndIntervalLocked) setSelectedSymbol(item.symbol);
                }
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                borderWidth: 1,
                borderStyle: "solid",
                borderColor: "#2a3b54",
                background: active ? "#e8f0fe" : "#ffffff",
                color: "#000000",
                borderRadius: 6,
                padding: "6px 8px",
                textAlign: "left",
                cursor: symbolAndIntervalLocked ? "not-allowed" : "pointer",
                fontVariantNumeric: "tabular-nums",
                opacity: symbolAndIntervalLocked ? 0.7 : 1,
              }}
            >
              <button
                type="button"
                aria-label={bookmarked ? "Remove bookmark" : "Bookmark"}
                onClick={(e) => handleToggleBookmark(e, item.symbol)}
                style={{
                  flexShrink: 0,
                  padding: 2,
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  color: bookmarked ? "#f59e0b" : "#9ca3af",
                  fontSize: 14,
                }}
              >
                {bookmarked ? "★" : "☆"}
              </button>
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  minWidth: 0,
                }}
              >
                <strong
                  style={{
                    flex: "0 0 50%",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={item.symbol}
                >
                  {item.symbol}
                </strong>
                <span
                  style={{
                    flex: "0 0 30%",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: "#000000",
                    fontSize: 13,
                    fontVariantNumeric: "tabular-nums",
                  }}
                  title={snapshot ? formatPrice(snapshot.price) : "--"}
                >
                  {snapshot ? formatPrice(snapshot.price) : "--"}
                </span>
                <span
                  style={{
                    flex: "0 0 20%",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: changeColor,
                    fontSize: 13,
                    textAlign: "left",
                    fontVariantNumeric: "tabular-nums",
                    paddingRight: 5,
                  }}
                  title={
                    change !== null
                      ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`
                      : "--"
                  }
                >
                  {change !== null
                    ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`
                    : "--"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

