"use client";

import { useMarketData } from "@/contexts/market-data-context";

function formatPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 1) return price.toFixed(4);
  return price.toFixed(6);
}

export function TickerList() {
  const { symbols, selectedSymbol, setSelectedSymbol, tickers } = useMarketData();

  return (
    <aside
      style={{
        width: 280,
        borderRight: "1px solid #243247",
        padding: 12,
        overflowY: "auto",
      }}
    >
      <h3 style={{ marginTop: 0 }}>Tickers</h3>
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
              onClick={() => setSelectedSymbol(item.symbol)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                borderWidth: 1,
                borderStyle: "solid",
                borderColor: "#2a3b54",
                background: active ? "#1f3b65" : "#111a2b",
                color: "#f1f6ff",
                borderRadius: 6,
                padding: "6px 10px",
                textAlign: "left",
                cursor: "pointer",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              <strong style={{ flexShrink: 0 }}>{item.symbol}</strong>
              <span style={{ color: "#e9edf8", fontSize: 13 }}>
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

