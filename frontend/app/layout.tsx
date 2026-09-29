import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Question Review · EvidenceForge",
  description: "Evidence-bound security questionnaire review workbench",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
