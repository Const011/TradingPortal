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

export function IndicatorControlPanel() {
  const { volumeProfileEnabled, setVolumeProfileEnabled } = useMarketData();

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
    </div>
  );
}
