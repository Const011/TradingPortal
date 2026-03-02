import { GatewayProvider } from "@/contexts/gateway-context";
import { MarketDataProvider } from "@/contexts/market-data-context";
import { MarketShell } from "@/components/market-shell";

export default function HomePage() {
  return (
    <GatewayProvider>
      <MarketDataProvider>
        <MarketShell />
      </MarketDataProvider>
    </GatewayProvider>
  );
}

