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
  background: "#111a2b",
  color: "#d6dfeb",
};

const toggleButtonActiveStyle = {
  ...toggleButtonStyle,
  background: "#1f3b65",
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
  } = useMarketData();

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      <span style={{ fontSize: 12, color: "#8b9bb4", marginRight: 4 }}>
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
      {volumeProfileEnabled && (
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, color: "#8b9bb4" }}>Window:</span>
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
