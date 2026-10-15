import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          bg: "#090d11",
          surface: "#0f1720",
          card: "#131d27",
          border: "#1e293b",
          text: "#f1f5f9",
          muted: "#94a3b8",
        },
        // Drawn from the Exigo Tech AI mark (Teal → Turquoise sweep).
        brand: {
          50: "#f0fdfa",
          100: "#ccfbf1",
          200: "#99f6e4",
          300: "#5eead4",
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#068987", // Exigo Tech Signature Deep Teal
          700: "#057472",
          800: "#0f5957",
          900: "#0c4342",
          950: "#042322",
        },
        ink: "#0a1c1a",
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
          "linear-gradient(135deg, #042322 0%, #0c4342 48%, #068987 100%)",
        "brand-gradient":
          "linear-gradient(135deg, var(--brand-gradient-from) 0%, var(--brand-gradient-via) 50%, var(--brand-gradient-to) 100%)",
      },
      boxShadow: {
        card: "0 1px 2px rgba(10, 28, 26, 0.04), 0 8px 24px rgba(10, 28, 26, 0.06)",
        "brand-glow": "0 4px 14px 0 var(--brand-glow)",
      },
    },
  },
  plugins: [],
};

export default config;
