"use client";

import type { StrategyResultsSummary } from "@/lib/strategy-results";
import { useMarketData } from "@/contexts/market-data-context";

function getPricePrecision(value: number): number {
  const abs = Math.abs(value);
  if (abs >= 1000) return 2;
  if (abs >= 1) return 4;
  return 6;
}

function formatPrice(value: number): string {
  const precision = getPricePrecision(value);
  return value.toFixed(precision);
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function formatCloseReason(reason: string): string {
  switch (reason) {
    case "stop":
      return "Stop";
    case "take_profit":
      return "Take Profit";
    case "end_of_data":
      return "End of data";
    case "manual":
      return "Manual";
    default:
      return reason;
  }
}

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
  marginTop: 8,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 12px",
  borderBottom: "2px solid #e0e0e0",
  fontWeight: 600,
  color: "#374151",
};

const tdStyle: React.CSSProperties = {
  padding: "8px 12px",
  borderBottom: "1px solid #e5e7eb",
};

const summaryStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "12px 16px",
  backgroundColor: "#f9fafb",
  borderRadius: 8,
  fontSize: 14,
  fontWeight: 500,
};

type StrategyResultsTableProps = {
  summary: StrategyResultsSummary | null;
};

export function StrategyResultsTable({ summary }: StrategyResultsTableProps) {
  const { selectedSymbol } = useMarketData();
  if (!summary || summary.trades.length === 0) {
    return null;
  }

  // Use the entry price of the first trade to define the "instrument" precision,
  // and keep points/total/avg formatted with exactly the same number of digits.
  const baseEntry = summary.trades[0]?.entryPrice ?? 0;
  const pointsPrecision = getPricePrecision(baseEntry);

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Entry</th>
            <th style={thStyle}>Type</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Entry Price</th>
            <th style={thStyle}>Close</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Close Price</th>
            <th style={thStyle}>Close Reason</th>
            <th style={{ ...thStyle, textAlign: "right" }}>Points</th>
          </tr>
        </thead>
        <tbody>
          {summary.trades.map((t, idx) => (
            <tr key={idx}>
              <td style={tdStyle}>{formatDateTime(t.entryDateTime)}</td>
              <td style={tdStyle}>
                <span
                  style={{
                    color: t.side === "long" ? "#16a34a" : "#dc2626",
                    fontWeight: 500,
                  }}
                >
                  {t.side.toUpperCase()}
                </span>
              </td>
              <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                {formatPrice(t.entryPrice)}
              </td>
              <td style={tdStyle}>{formatDateTime(t.closeDateTime)}</td>
              <td
                style={{
                  ...tdStyle,
                  textAlign: "right",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {formatPrice(t.closePrice)}
              </td>
              <td style={tdStyle}>{formatCloseReason(t.closeReason)}</td>
              <td
                style={{
                  ...tdStyle,
                  textAlign: "right",
                  color: t.points >= 0 ? "#16a34a" : "#dc2626",
                  fontWeight: 500,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {t.points.toFixed(pointsPrecision)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={summaryStyle}>
        Total: {summary.totalPoints.toFixed(pointsPrecision)} pts · Avg:{" "}
        {summary.avgPointsPerTrade.toFixed(pointsPrecision)} pts/trade ({summary.trades.length}{" "}
        trades) · Win rate: {summary.winRatePercent.toFixed(1)}%
      </div>
    </div>
  );
}
