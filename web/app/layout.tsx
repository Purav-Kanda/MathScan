import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import "katex/dist/katex.min.css"; // KaTeX ships its own CSS for math layout/spacing -- without this, formulas render as plain unstyled text instead of properly typeset math.

// next/font/google downloads and self-hosts the font at build time --
// no request to Google's servers from the user's browser, which is both
// faster and more private than a normal <link> tag pulling from a CDN.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "MathScan",
  description: "Turn handwritten math into LaTeX.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} font-sans antialiased`}>{children}</body>
    </html>
  );
}
