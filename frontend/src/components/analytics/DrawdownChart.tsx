"use client";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface DrawdownChartProps {
  data: number[];
  height?: number;
}

export function DrawdownChart({ data, height = 300 }: DrawdownChartProps) {
  // Calculate drawdown % from equity curve
  const drawdown: number[] = [];
  let peak = data[0] ?? 0;
  for (const val of data) {
    if (val > peak) peak = val;
    drawdown.push(peak === 0 ? 0 : ((val - peak) / peak) * 100);
  }

  return (
    <Plot
      data={[
        {
          y: drawdown,
          type: "scatter",
          mode: "lines",
          line: { color: "#EF4444", width: 2 },
          fill: "tozeroy",
          fillcolor: "rgba(239, 68, 68, 0.15)",
        },
      ]}
      layout={{
        title: { text: "Drawdown %", font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        xaxis: { showgrid: false },
        yaxis: { gridcolor: "#E5E5E5", ticksuffix: "%" },
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
