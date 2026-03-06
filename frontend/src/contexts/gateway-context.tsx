"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

const GATEWAY_STORAGE_KEY = "trading-portal-gateway";

export type GatewayMode = "simulation" | "trade";

export type GatewayConfig = {
  mode: GatewayMode;
  /** Backend market: "spot" or "linear" (for ticker bookmarks key). */
  market?: string;
  symbol?: string;
  interval?: string;
  bars_window?: number;
};

type GatewayContextValue = {
  backendBaseUrl: string;
  gatewayConfig: GatewayConfig | null;
  connecting: boolean;
  connectError: string | null;
  connect: (mode: GatewayMode, port: number) => Promise<void>;
  disconnect: () => void;
};

const GatewayContext = createContext<GatewayContextValue | null>(null);

const SIMULATION_PORT = 9000;
const TRADING_PORT = 9001;

function loadStoredGateway(): { mode: GatewayMode; port: number } | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(GATEWAY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { mode?: string; port?: number };
    if (
      (parsed.mode === "simulation" || parsed.mode === "trade") &&
      typeof parsed.port === "number" &&
      parsed.port >= 1 &&
      parsed.port <= 65535
    ) {
      return { mode: parsed.mode, port: parsed.port };
    }
  } catch {
    // ignore
  }
  return null;
}

function storeGateway(mode: GatewayMode, port: number): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    GATEWAY_STORAGE_KEY,
    JSON.stringify({ mode, port })
  );
}

type GatewayProviderProps = {
  children: ReactNode;
};

export function GatewayProvider({ children }: GatewayProviderProps) {
  const [backendBaseUrl, setBackendBaseUrl] = useState<string>("");
  const [gatewayConfig, setGatewayConfig] = useState<GatewayConfig | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const connect = useCallback(async (mode: GatewayMode, port: number) => {
    const url = `http://localhost:${port}`;
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch(`${url}/api/v1/mode`);
      if (!res.ok) {
        throw new Error(`Gateway unreachable (${res.status})`);
      }
      const data = (await res.json()) as {
        mode: string;
        market?: string;
        symbol?: string;
        interval?: string;
        bars_window?: number;
      };
      const config: GatewayConfig = {
        mode: data.mode === "trading" ? "trade" : "simulation",
        market: data.market === "linear" ? "linear" : "spot",
        symbol: data.symbol,
        interval: data.interval,
        bars_window: data.bars_window,
      };
      setBackendBaseUrl(url);
      setGatewayConfig(config);
      storeGateway(mode, port);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Connection failed";
      setConnectError(message);
      setBackendBaseUrl("");
      setGatewayConfig(null);
    } finally {
      setConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    setBackendBaseUrl("");
    setGatewayConfig(null);
    setConnectError(null);
  }, []);

  useEffect(() => {
    const stored = loadStoredGateway();
    if (stored) {
      void connect(stored.mode, stored.port);
    } else {
      void connect("simulation", SIMULATION_PORT);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, []);

  const value = useMemo<GatewayContextValue>(
    () => ({
      backendBaseUrl,
      gatewayConfig,
      connecting,
      connectError,
      connect,
      disconnect,
    }),
    [backendBaseUrl, gatewayConfig, connecting, connectError, connect]
  );

  return (
    <GatewayContext.Provider value={value}>{children}</GatewayContext.Provider>
  );
}

export function useGateway(): GatewayContextValue {
  const context = useContext(GatewayContext);
  if (!context) {
    throw new Error("useGateway must be used within GatewayProvider");
  }
  return context;
}

export { SIMULATION_PORT, TRADING_PORT };
