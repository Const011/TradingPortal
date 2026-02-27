import "./globals.css";
import type { Metadata, Viewport } from "next";
import { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Trading Portal",
  description: "Spot market watch and chart workspace",
};

export const viewport: Viewport = {
  colorScheme: "light",
};

type RootLayoutProps = {
  children: ReactNode;
};

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" style={{ colorScheme: "light" }}>
      <body style={{ background: "#ffffff", color: "#000000" }}>{children}</body>
    </html>
  );
}

