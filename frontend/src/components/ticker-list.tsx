"use client";

import { useMarketData } from "@/contexts/market-data-context";

export function TickerList() {
  const { symbols, selectedSymbol, setSelectedSymbol, tickers } = useMarketData();

  return (
    <aside
      style={{
        width: 260,
        borderRight: "1px solid #243247",
        padding: 12,
        overflowY: "auto",
      }}
    >
      <h3 style={{ marginTop: 0 }}>Tickers</h3>
      <div style={{ display: "grid", gap: 8 }}>
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
                border: "1px solid #2a3b54",
                background: active ? "#1f3b65" : "#111a2b",
                color: "#f1f6ff",
                borderRadius: 6,
                padding: "8px 10px",
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              <strong>{item.symbol}</strong>
              <div style={{ fontSize: 12, opacity: 0.8 }}>
                {item.baseCoin}/{item.quoteCoin}
              </div>
              <div style={{ marginTop: 4, fontSize: 12 }}>
                {snapshot ? snapshot.price.toFixed(6) : "--"}
              </div>
              <div style={{ fontSize: 12, color: changeColor }}>
                {change !== null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "--"}
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

