import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // WHY a rewrite instead of hardcoding the backend URL in every fetch call:
  // during local dev, the browser and the FastAPI server run on different
  // ports (3000 vs 8000). A rewrite lets frontend code call same-origin
  // paths like `/api/ocr/pdf`, and Next.js quietly forwards them to the
  // real backend -- so this is the ONE place the backend's address is
  // configured, instead of scattered across every component.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
