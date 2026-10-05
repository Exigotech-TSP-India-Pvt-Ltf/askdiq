import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Drawn from the Exigo Tech mark (indigo → violet → magenta sweep).
        brand: {
          50: "#f2f0ff",
          100: "#e4e0ff",
          200: "#c3baff",
          300: "#9c8bff",
          400: "#7257f5",
          500: "#5433e0",
          600: "#4324c2",
          700: "#361c9c",
          800: "#2a1677",
          900: "#1c0e52",
          950: "#12083a",
        },
        ink: "#0e0b1f",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
      backgroundImage: {
        "brand-panel":
          "linear-gradient(135deg, #1c0e52 0%, #34199a 48%, #5433e0 100%)",
      },
      boxShadow: {
        card: "0 1px 2px rgba(14, 11, 31, 0.04), 0 8px 24px rgba(14, 11, 31, 0.06)",
      },
    },
  },
  plugins: [],
};

export default config;
