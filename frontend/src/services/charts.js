import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";

ChartJS.register(ArcElement, BarElement, CategoryScale, Filler, Legend, LineElement, LinearScale, PointElement, Tooltip);

export const chartPalette = ["#ff4d4d", "#ff6a4d", "#ff8a4d", "#ffae63", "#d74444", "#ffb07c"];

export const baseChartOptions = {
  maintainAspectRatio: false,
  interaction: {
    intersect: false,
    mode: "index",
  },
  plugins: {
    legend: {
      labels: {
        color: "#f4d6d0",
        font: {
          family: "Manrope",
          size: 12,
          weight: "600",
        },
      },
    },
    tooltip: {
      backgroundColor: "rgba(24, 4, 4, 0.96)",
      borderColor: "rgba(255, 96, 96, 0.35)",
      borderWidth: 1,
      titleColor: "#fff7f5",
      bodyColor: "#f8d4cf",
      displayColors: true,
    },
  },
  scales: {
    x: {
      grid: {
        color: "rgba(255, 119, 119, 0.08)",
      },
      ticks: {
        color: "#c9a8a1",
        font: {
          family: "Manrope",
          size: 11,
        },
      },
    },
    y: {
      grid: {
        color: "rgba(255, 119, 119, 0.08)",
      },
      ticks: {
        color: "#c9a8a1",
        font: {
          family: "Manrope",
          size: 11,
        },
      },
    },
  },
};

export const minimalAxisOptions = {
  ...baseChartOptions,
  plugins: {
    ...baseChartOptions.plugins,
    legend: { display: false },
  },
};
