"use client";

import { useMarketData } from "@/contexts/market-data-context";
import { PriceChart } from "@/components/price-chart";
import { TickerList } from "@/components/ticker-list";
import { backendMode } from "@/lib/api/market";

function formatPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 1) return price.toFixed(4);
  return price.toFixed(6);
}

function formatVolume(volume: number): string {
  if (volume >= 1e9) return `${(volume / 1e9).toFixed(2)}B`;
  if (volume >= 1e6) return `${(volume / 1e6).toFixed(2)}M`;
  if (volume >= 1e3) return `${(volume / 1e3).toFixed(2)}K`;
  return volume.toFixed(0);
}

export function MarketShell() {
  const { selectedSymbol, currentBar, latestTick, loading, error } = useMarketData();

  return (
    <main style={{ display: "flex", minHeight: "100vh" }}>
      <TickerList />
      <section style={{ flex: 1, padding: 16 }}>
        <header style={{ marginBottom: 16 }}>
          <h2 style={{ margin: 0 }}>
            Trading Portal
            {backendMode === "trading" ? (
              <span style={{ marginLeft: 8, fontSize: "0.75em", opacity: 0.9 }}>
                — TRADING
              </span>
            ) : (
              <span style={{ marginLeft: 8, fontSize: "0.75em", opacity: 0.7 }}>
                — SIMULATION
              </span>
            )}
          </h2>
          <p style={{ margin: "8px 0 0", opacity: 0.8, fontVariantNumeric: "tabular-nums" }}>
            {selectedSymbol || "Select a symbol"}
            {currentBar != null ? (
              <>
                {" "}
                | Last: {formatPrice(currentBar.close)}
                {" | "}
                O: {formatPrice(currentBar.open)} H: {formatPrice(currentBar.high)} L:{" "}
                {formatPrice(currentBar.low)} C: {formatPrice(currentBar.close)}
                {" | "}
                Vol: {formatVolume(currentBar.volume)}
              </>
            ) : latestTick ? (
              <> | Last: {formatPrice(latestTick.price)}</>
            ) : (
              ""
            )}
          </p>
          {loading ? <p>Loading market metadata...</p> : null}
          {error ? <p style={{ color: "#ff7f7f" }}>{error}</p> : null}
        </header>
        <PriceChart />
      </section>
    </main>
  );
}

