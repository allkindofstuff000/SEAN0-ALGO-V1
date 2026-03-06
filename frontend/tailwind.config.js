export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Rajdhani", "sans-serif"],
        body: ["Manrope", "sans-serif"],
      },
      colors: {
        mega: {
          950: "#0b0000",
          900: "#140000",
          850: "#1c0000",
          800: "#240202",
        },
        accent: {
          red: "#ff4d4d",
          crimson: "#ff6a5e",
          orange: "#ff9a3d",
          ember: "#ff7c56",
          ash: "#f5d7d3",
        },
      },
      boxShadow: {
        ember: "0 0 0 1px rgba(255,88,88,0.12), 0 16px 36px rgba(0,0,0,0.35), 0 0 36px rgba(255,78,78,0.12)",
        "ember-alert": "0 0 0 1px rgba(255,107,107,0.18), 0 18px 42px rgba(83,0,0,0.42), 0 0 40px rgba(255,58,58,0.18)",
      },
      keyframes: {
        "pulse-red": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(255,85,85,0.18)" },
          "50%": { boxShadow: "0 0 0 12px rgba(255,85,85,0)" },
        },
        drift: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-6px)" },
        },
        sheen: {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
      },
      animation: {
        "pulse-red": "pulse-red 2.2s ease-in-out infinite",
        drift: "drift 6s ease-in-out infinite",
        sheen: "sheen 3.4s linear infinite",
      },
    },
  },
  plugins: [],
};
