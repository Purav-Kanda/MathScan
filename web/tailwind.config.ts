import type { Config } from "tailwindcss";

// Design system, kept in one place so every component pulls from the same
// palette instead of components inventing their own one-off colors.
// "accent" = the one confident color (deep teal), used sparingly and
// deliberately -- buttons, links, focus states, progress indicators.
// Everything else stays near-black/white/gray so the accent actually reads
// as intentional instead of getting lost among five other loud colors.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: {
          50: "#f0fdfa",
          100: "#ccfbf1",
          400: "#2dd4bf",
          600: "#0d9488",
          700: "#0f766e",
          800: "#115e59",
          900: "#134e4a",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
      },
      boxShadow: {
        soft: "0 2px 8px 0 rgb(0 0 0 / 0.06)",
        card: "0 1px 3px 0 rgb(0 0 0 / 0.08), 0 1px 2px -1px rgb(0 0 0 / 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
