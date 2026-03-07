"use client";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface HeatmapProps {
  z: number[][];
  x: string[];
  y: string[];
  title?: string;
  height?: number;
}

export function Heatmap({ z, x, y, title = "Heatmap", height = 500 }: HeatmapProps) {
  return (
    <Plot
      data={[
        {
          z,
          x,
          y,
          type: "heatmap",
          colorscale: [
            [0, "#EF4444"],
            [0.5, "#F5F5F4"],
            [1, "#16A34A"],
          ],
          hoverongaps: false,
        },
      ]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 80, l: 120 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { family: "Inter, system-ui, sans-serif" },
        xaxis: { side: "bottom" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
