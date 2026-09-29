import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "InfraBrix Voice",
  description: "Talk to your on-call DevOps agent. Built on the AssemblyAI Voice Agent API.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
