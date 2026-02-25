"use client";

import { useMarketData } from "@/contexts/market-data-context";
import { PriceChart } from "@/components/price-chart";
import { TickerList } from "@/components/ticker-list";

export function MarketShell() {
  const { selectedSymbol, latestTick, loading, error } = useMarketData();

  return (
    <main style={{ display: "flex", minHeight: "100vh" }}>
      <TickerList />
      <section style={{ flex: 1, padding: 16 }}>
        <header style={{ marginBottom: 16 }}>
          <h2 style={{ margin: 0 }}>Trading Portal</h2>
          <p style={{ margin: "8px 0 0", opacity: 0.8 }}>
            {selectedSymbol || "Select a symbol"}{" "}
            {latestTick ? `| Last: ${latestTick.price.toFixed(6)}` : ""}
          </p>
          {loading ? <p>Loading market metadata...</p> : null}
          {error ? <p style={{ color: "#ff7f7f" }}>{error}</p> : null}
        </header>
        <PriceChart />
      </section>
    </main>
  );
}

