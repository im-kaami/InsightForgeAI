import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "InsightForge",
  description: "Schema-agnostic data analysis",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-muted/20 font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
