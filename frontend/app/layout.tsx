import "./globals.css";
import type { Metadata, Viewport } from "next";
import { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Trading Portal",
  description: "Spot market watch and chart workspace",
};

export const viewport: Viewport = {
  colorScheme: "dark",
};

type RootLayoutProps = {
  children: ReactNode;
};

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" style={{ colorScheme: "dark" }}>
      <body style={{ background: "#0c111d", color: "#e9edf8" }}>{children}</body>
    </html>
  );
}

