"use client";

import { useState } from "react";
import {
  useGateway,
  SIMULATION_PORT,
  TRADING_PORT,
  type GatewayMode,
} from "@/contexts/gateway-context";

const buttonStyle = {
  padding: "6px 12px",
  fontSize: 13,
  borderWidth: 1,
  borderStyle: "solid" as const,
  borderColor: "#2a3b54",
  borderRadius: 6,
  cursor: "pointer" as const,
  background: "#ffffff",
  color: "#000000",
};

const buttonActiveStyle = {
  ...buttonStyle,
  background: "#e8f0fe",
  borderColor: "#3b82f6",
};

const inputStyle = {
  ...buttonStyle,
  padding: "4px 8px",
  width: 84, // 1.5× prior 56px — five-digit ports + spinner
  fontSize: 13,
};

export function GatewaySelector() {
  const {
    gatewayConfig,
    connecting,
    connectError,
    connect,
  } = useGateway();
  const [mode, setMode] = useState<GatewayMode>("simulation");
  const [port, setPort] = useState(TRADING_PORT);

  const handleConnect = (): void => {
    const p = mode === "simulation" ? SIMULATION_PORT : port;
    void connect(mode, p);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
        marginBottom: 8,
      }}
    >
      <span style={{ fontSize: 12, color: "#5f6368" }}>Gateway:</span>
      {(["simulation", "trade"] as const).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => setMode(m)}
          style={mode === m ? buttonActiveStyle : buttonStyle}
        >
          {m === "simulation" ? "Simulation" : "Trade"}
        </button>
      ))}
      {mode === "trade" && (
        <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ fontSize: 12, color: "#5f6368" }}>Port:</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (!Number.isNaN(v) && v >= 1 && v <= 65535) setPort(v);
            }}
            style={inputStyle}
          />
        </label>
      )}
      <button
        type="button"
        onClick={handleConnect}
        disabled={connecting}
        style={buttonStyle}
      >
        {connecting ? "Connecting…" : "Connect"}
      </button>
      {connectError && (
        <span style={{ fontSize: 12, color: "#e74c3c" }}>{connectError}</span>
      )}
        {gatewayConfig && !connectError && (
          <span style={{ fontSize: 12, color: "#2ecc71" }}>
            {gatewayConfig.mode === "trade" ? "Trading" : "Simulation"} connected
          </span>
        )}
    </div>
  );
}
