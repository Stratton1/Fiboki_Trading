"use client";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface EquityCurveProps {
  data: number[];
  height?: number;
  title?: string;
}

export function EquityCurve({ data, height = 300, title = "Equity Curve" }: EquityCurveProps) {
  return (
    <Plot
      data={[{
        y: data, type: "scatter", mode: "lines",
        line: { color: "#16A34A", width: 2 },
        fill: "tozeroy", fillcolor: "rgba(22, 163, 74, 0.1)",
      }]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height, margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        xaxis: { showgrid: false }, yaxis: { gridcolor: "#E5E5E5" },
        font: { family: "Inter, system-ui, sans-serif" },
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
