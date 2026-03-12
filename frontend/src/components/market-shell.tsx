"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GatewaySelector } from "@/components/gateway-selector";
import { useMarketData } from "@/contexts/market-data-context";
import { PriceChart } from "@/components/price-chart";
import { TickerList } from "@/components/ticker-list";

const TICKER_PANEL_MIN_WIDTH = 180;
const TICKER_PANEL_MAX_WIDTH = 500;
const TICKER_PANEL_DEFAULT_WIDTH = 280;

function formatPrice(price: number): string {
  const abs = Math.abs(price);
  if (abs >= 1000) return price.toFixed(2);
  if (abs >= 1) return price.toFixed(4);
  return price.toFixed(6);
}

function formatVolume(volume: number): string {
  if (volume >= 1e9) return `${(volume / 1e9).toFixed(2)}B`;
  if (volume >= 1e6) return `${(volume / 1e6).toFixed(2)}M`;
  if (volume >= 1e3) return `${(volume / 1e3).toFixed(2)}K`;
  return volume.toFixed(0);
}

export function MarketShell() {
  const {
    selectedSymbol,
    currentBar,
    latestTick,
    loading,
    error,
    gatewayConfig,
  } = useMarketData();

  const [tickerPanelWidth, setTickerPanelWidth] = useState(TICKER_PANEL_DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [handleHover, setHandleHover] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(TICKER_PANEL_DEFAULT_WIDTH);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    startXRef.current = e.pageX;
    startWidthRef.current = tickerPanelWidth;
    setIsResizing(true);
  }, [tickerPanelWidth]);

  useEffect(() => {
    if (!isResizing) return;
    const onMove = (e: MouseEvent) => {
      const delta = e.pageX - startXRef.current;
      const next = Math.min(
        TICKER_PANEL_MAX_WIDTH,
        Math.max(TICKER_PANEL_MIN_WIDTH, startWidthRef.current + delta)
      );
      setTickerPanelWidth(next);
    };
    const onUp = () => setIsResizing(false);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

  return (
    <main style={{ display: "flex", minHeight: "100vh" }}>
      <div style={{ display: "flex", flexShrink: 0 }}>
        <TickerList width={tickerPanelWidth} />
        <div
          role="separator"
          aria-label="Resize ticker panel"
          onMouseDown={handleResizeStart}
          onMouseEnter={() => setHandleHover(true)}
          onMouseLeave={() => setHandleHover(false)}
          style={{
            width: 8,
            cursor: "col-resize",
            background: isResizing ? "#2a3b54" : handleHover ? "rgba(42,59,84,0.5)" : "transparent",
            flexShrink: 0,
          }}
        />
      </div>
      <section style={{ flex: 1, minWidth: 0, padding: 16 }}>
        <header style={{ marginBottom: 16 }}>
          <GatewaySelector />
          <h2 style={{ margin: 0 }}>
            Trading Portal
            {gatewayConfig?.mode === "trade" ? (
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

