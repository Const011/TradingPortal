"use client";

import { useMarketData } from "@/contexts/market-data-context";

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
    volumeProfileEnabled,
    setVolumeProfileEnabled,
    volumeProfileWindow,
    setVolumeProfileWindow,
    supportResistanceEnabled,
    setSupportResistanceEnabled,
    orderBlocksEnabled,
    setOrderBlocksEnabled,
  } = useMarketData();

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
