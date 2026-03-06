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
import { Bar, Line } from "react-chartjs-2";

import { PanelShell } from "./ui/PanelShell";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, ArcElement, Tooltip, Legend, Filler);

const baseOptions = {
  maintainAspectRatio: false,
  responsive: true,
  plugins: {
    legend: {
      labels: {
        color: "#cbd5e1",
        font: { family: "IBM Plex Sans" },
      },
    },
  },
  scales: {
    x: {
      ticks: { color: "#94a3b8" },
      grid: { color: "rgba(148, 163, 184, 0.08)" },
    },
    y: {
      ticks: { color: "#94a3b8" },
      grid: { color: "rgba(148, 163, 184, 0.08)" },
    },
  },
};

function ChartCard({ title, subtitle, children }) {
  return (
    <div className="chart-shell rounded-[24px] border border-white/8 bg-ink-900/55 p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">{subtitle}</div>
          <div className="mt-1 font-display text-lg font-semibold text-white">{title}</div>
        </div>
      </div>
      <div className="h-64">{children}</div>
    </div>
  );
}

export default function ChartsPanel({ performance }) {
  const distribution = performance?.signalScoreDistribution || [];
  const sessions = performance?.sessionPerformance || [];
  const thresholds = performance?.thresholdEvolution || [];
  const timeline = performance?.signalsOverTime || [];

  const distributionData = {
    labels: distribution.map((item) => item.range),
    datasets: [
      {
        label: "Signals",
        data: distribution.map((item) => item.count),
        borderRadius: 14,
        backgroundColor: ["#4af2e3", "#4f8cff", "#8f6bff", "#ffbf5e", "#2fd38f"],
      },
    ],
  };

  const sessionData = {
    labels: sessions.map((item) => item.session),
    datasets: [
      {
        label: "Win rate %",
        data: sessions.map((item) => item.winRate),
        borderRadius: 10,
        backgroundColor: "rgba(47, 211, 143, 0.8)",
        borderColor: "#2fd38f",
      },
    ],
  };

  const thresholdData = {
    labels: thresholds.map((item) => item.timestamp || "--"),
    datasets: [
      {
        label: "Threshold",
        data: thresholds.map((item) => item.threshold),
        borderColor: "#8f6bff",
        backgroundColor: "rgba(143, 107, 255, 0.18)",
        pointBackgroundColor: "#c4b5fd",
        tension: 0.3,
        fill: true,
      },
    ],
  };

  const timelineData = {
    labels: timeline.map((item) => item.timestamp),
    datasets: [
      {
        label: "Signals",
        data: timeline.map((item) => item.count),
        borderColor: "#4af2e3",
        backgroundColor: "rgba(74, 242, 227, 0.12)",
        pointBackgroundColor: "#4af2e3",
        pointRadius: 3,
        tension: 0.32,
        fill: true,
      },
    ],
  };

  return (
    <PanelShell title="Charts Section" subtitle="Analytics" accent="blue">
      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard title="Signal Score Distribution" subtitle="Score buckets">
          <Bar data={distributionData} options={baseOptions} />
        </ChartCard>
        <ChartCard title="Session Performance" subtitle="Win-rate by session">
          <Bar data={sessionData} options={baseOptions} />
        </ChartCard>
        <ChartCard title="Threshold Evolution" subtitle="Adaptive learning history">
          <Line data={thresholdData} options={baseOptions} />
        </ChartCard>
        <ChartCard title="Signals Over Time" subtitle="Recent routing cadence">
          <Line data={timelineData} options={baseOptions} />
        </ChartCard>
      </div>
    </PanelShell>
  );
}
