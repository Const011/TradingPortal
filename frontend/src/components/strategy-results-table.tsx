"use client";

import type { StrategyResultsSummary } from "@/lib/strategy-results";

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
  if (!summary || summary.trades.length === 0) {
    return null;
  }

  return (
    <div style={{ marginTop: 16, overflowX: "auto" }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Entry</th>
            <th style={thStyle}>Type</th>
            <th style={thStyle}>Close</th>
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
              <td style={tdStyle}>{formatDateTime(t.closeDateTime)}</td>
              <td style={tdStyle}>{formatCloseReason(t.closeReason)}</td>
              <td
                style={{
                  ...tdStyle,
                  textAlign: "right",
                  color: t.points >= 0 ? "#16a34a" : "#dc2626",
                  fontWeight: 500,
                }}
              >
                {t.points >= 0 ? "+" : ""}
                {t.points.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={summaryStyle}>
        Total: {summary.totalPoints >= 0 ? "+" : ""}
        {summary.totalPoints.toFixed(2)} pts · Avg:{" "}
        {summary.avgPointsPerTrade >= 0 ? "+" : ""}
        {summary.avgPointsPerTrade.toFixed(2)} pts/trade ({summary.trades.length}{" "}
        trades)
      </div>
    </div>
  );
}
