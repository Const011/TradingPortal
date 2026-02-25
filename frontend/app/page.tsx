import { MarketDataProvider } from "@/contexts/market-data-context";
import { MarketShell } from "@/components/market-shell";

export default function HomePage() {
  return (
    <MarketDataProvider>
      <MarketShell />
    </MarketDataProvider>
  );
}

