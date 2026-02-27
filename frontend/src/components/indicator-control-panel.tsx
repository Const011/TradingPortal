"use client";

import { useMarketData } from "@/contexts/market-data-context";
import { downloadStrategyData } from "@/lib/strategy-data-export";

const toggleButtonStyle = {
  padding: "8px 16px",
  fontSize: 14,
  borderWidth: 1,
  borderStyle: "solid" as const,
  borderColor: "#2a3b54",
  borderRadius: 6,
  cursor: "pointer" as const,
  background: "#ffffff",
  color: "#000000",
};

const toggleButtonActiveStyle = {
  ...toggleButtonStyle,
  background: "#e8f0fe",
  borderColor: "#3b82f6",
};

const inputStyle = {
  ...toggleButtonStyle,
  padding: "6px 10px",
  width: 72,
  fontSize: 13,
};

export function IndicatorControlPanel() {
  const {
    selectedSymbol,
    chartInterval,
    candles,
    volumeProfile,
    supportResistance,
    orderBlocks,
    structure,
    strategySignals,
    obShowBull,
    setObShowBull,
    obShowBear,
    setObShowBear,
    swingLabelsShow,
    setSwingLabelsShow,
    volumeProfileEnabled,
    setVolumeProfileEnabled,
    volumeProfileWindow,
    setVolumeProfileWindow,
    supportResistanceEnabled,
    setSupportResistanceEnabled,
    orderBlocksEnabled,
    setOrderBlocksEnabled,
    structureEnabled,
    setStructureEnabled,
    candleColoringEnabled,
    setCandleColoringEnabled,
    strategyMarkers,
    setStrategyMarkers,
  } = useMarketData();

  const handleDownloadStrategyData = (): void => {
    downloadStrategyData({
      symbol: selectedSymbol,
      interval: chartInterval,
      candles,
      volumeProfile,
      supportResistance,
      orderBlocks,
      structure,
      strategySignals,
    });
  };

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      <span style={{ fontSize: 12, color: "#5f6368", marginRight: 4 }}>
        Indicators:
      </span>
      <button
        type="button"
        onClick={() => setVolumeProfileEnabled(!volumeProfileEnabled)}
        style={volumeProfileEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        Volume Profile
      </button>
      <button
        type="button"
        onClick={() => setSupportResistanceEnabled(!supportResistanceEnabled)}
        style={supportResistanceEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        S/R
      </button>
      <button
        type="button"
        onClick={() => setOrderBlocksEnabled(!orderBlocksEnabled)}
        style={orderBlocksEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        OB
      </button>
      {orderBlocksEnabled && (
        <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ fontSize: 12, color: "#5f6368" }}>Bull:</span>
          <input
            type="number"
            min={0}
            max={50}
            value={obShowBull}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!Number.isNaN(v) && v >= 0 && v <= 50) setObShowBull(v);
            }}
            style={{ ...inputStyle, width: 44 }}
          />
          <span style={{ fontSize: 12, color: "#5f6368" }}>Bear:</span>
          <input
            type="number"
            min={0}
            max={50}
            value={obShowBear}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!Number.isNaN(v) && v >= 0 && v <= 50) setObShowBear(v);
            }}
            style={{ ...inputStyle, width: 44 }}
          />
        </label>
      )}
      <button
        type="button"
        onClick={() => setStructureEnabled(!structureEnabled)}
        style={structureEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        Structure
      </button>
      {structureEnabled && (
        <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ fontSize: 12, color: "#5f6368" }}>Swings:</span>
          <input
            type="number"
            min={0}
            max={50}
            value={swingLabelsShow}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!Number.isNaN(v) && v >= 0 && v <= 50) setSwingLabelsShow(v);
            }}
            style={{ ...inputStyle, width: 44 }}
          />
        </label>
      )}
      <button
        type="button"
        onClick={() => setCandleColoringEnabled(!candleColoringEnabled)}
        style={candleColoringEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        Candle Coloring
      </button>
      <button
        type="button"
        onClick={handleDownloadStrategyData}
        title="Download bar data, indicators, orders and trailing stops for AI review"
        style={toggleButtonStyle}
      >
        Export for AI
      </button>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <span style={{ fontSize: 12, color: "#5f6368" }}>Markers:</span>
        {(["off", "simulation", "trade"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setStrategyMarkers(mode)}
            style={strategyMarkers === mode ? toggleButtonActiveStyle : toggleButtonStyle}
          >
            {mode === "off" ? "Off" : mode === "simulation" ? "Sim" : "Trade"}
          </button>
        ))}
      </div>
      {volumeProfileEnabled && (
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, color: "#5f6368" }}>Window:</span>
          <input
            type="number"
            min={100}
            max={10000}
            step={100}
            value={volumeProfileWindow}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!Number.isNaN(v) && v >= 100 && v <= 10000) {
                setVolumeProfileWindow(v);
              }
            }}
            style={inputStyle}
          />
        </label>
      )}
    </div>
  );
}
