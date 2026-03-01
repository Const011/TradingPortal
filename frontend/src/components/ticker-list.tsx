"use client";

import { useMarketData } from "@/contexts/market-data-context";

function formatPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 1) return price.toFixed(4);
  return price.toFixed(6);
}

export function TickerList() {
  const { symbols, selectedSymbol, setSelectedSymbol, tickers, symbolAndIntervalLocked } =
    useMarketData();

  return (
    <aside
      style={{
        width: 280,
        borderRight: "1px solid #e0e0e0",
        padding: 12,
        overflowY: "auto",
      }}
    >
      <h3 style={{ marginTop: 0 }}>Tickers</h3>
      {symbolAndIntervalLocked && (
        <p style={{ margin: "4px 0 8px", fontSize: 12, opacity: 0.7 }}>
          Fixed by gateway config
        </p>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {symbols.map((item) => {
          const active = item.symbol === selectedSymbol;
          const snapshot = tickers[item.symbol];
          const change = snapshot ? snapshot.change_24h_percent : null;
          const changeColor =
            change === null ? "#9bb0d1" : change >= 0 ? "#2ecc71" : "#e74c3c";
          return (
            <button
              key={item.symbol}
              type="button"
              disabled={symbolAndIntervalLocked}
              onClick={() => !symbolAndIntervalLocked && setSelectedSymbol(item.symbol)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                borderWidth: 1,
                borderStyle: "solid",
                borderColor: "#2a3b54",
                background: active ? "#e8f0fe" : "#ffffff",
                color: "#000000",
                borderRadius: 6,
                padding: "6px 10px",
                textAlign: "left",
                cursor: symbolAndIntervalLocked ? "not-allowed" : "pointer",
                fontVariantNumeric: "tabular-nums",
                opacity: symbolAndIntervalLocked ? 0.7 : 1,
              }}
            >
              <strong style={{ flexShrink: 0 }}>{item.symbol}</strong>
              <span style={{ color: "#000000", fontSize: 13 }}>
                {snapshot ? formatPrice(snapshot.price) : "--"}
              </span>
              <span style={{ color: changeColor, fontSize: 13, minWidth: 52, textAlign: "right" }}>
                {change !== null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "--"}
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

