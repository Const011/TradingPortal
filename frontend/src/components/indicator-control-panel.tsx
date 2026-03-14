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
    volumeProfileEnabled,
    setVolumeProfileEnabled,
    supportResistanceEnabled,
    setSupportResistanceEnabled,
    orderBlocksEnabled,
    setOrderBlocksEnabled,
    structureEnabled,
    setStructureEnabled,
    candleColoringEnabled,
    setCandleColoringEnabled,
    strategyMarkersEnabled,
    setStrategyMarkersEnabled,
    symbolAndIntervalLocked,
    preciseSimulationEnabled,
    setPreciseSimulationEnabled,
    runPreciseSimulation,
    preciseSimulationRunning,
    cumulativeVolumeDeltaEnabled,
    setCumulativeVolumeDeltaEnabled,
  } = useMarketData();

  const handleDownloadStrategyData = (): void => {
    if (symbolAndIntervalLocked) {
      return;
    }
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

  const handleTogglePreciseSimulation = async (): Promise<void> => {
    if (preciseSimulationEnabled) {
      setPreciseSimulationEnabled(false);
      return;
    }
    await runPreciseSimulation();
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
      <button
        type="button"
        onClick={() => setStructureEnabled(!structureEnabled)}
        style={structureEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        Structure
      </button>
      <button
        type="button"
        onClick={() => setCumulativeVolumeDeltaEnabled(!cumulativeVolumeDeltaEnabled)}
        style={cumulativeVolumeDeltaEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        CVD
      </button>
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
        disabled={symbolAndIntervalLocked}
        style={toggleButtonStyle}
      >
        Export for AI
      </button>
      <button
        type="button"
        onClick={() => {
          void handleTogglePreciseSimulation();
        }}
        disabled={symbolAndIntervalLocked}
        style={preciseSimulationEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
      >
        {preciseSimulationRunning ? "Running..." : "Precise simulate"}
      </button>
      <button
        type="button"
        onClick={() => setStrategyMarkersEnabled(!strategyMarkersEnabled)}
        style={strategyMarkersEnabled ? toggleButtonActiveStyle : toggleButtonStyle}
        title="Strategy entry/exit markers and stop segments"
      >
        Markers
      </button>
    </div>
  );
}
