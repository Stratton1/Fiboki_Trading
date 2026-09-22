"use client";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface MiniSummaryProps {
  data: number[];
  color?: string;
  height?: number;
}

export function MiniSummary({ data, color = "#16A34A", height = 60 }: MiniSummaryProps) {
  return (
    <Plot
      data={[{
        y: data, type: "scatter", mode: "lines",
        line: { color, width: 1.5 },
        fill: "tozeroy", fillcolor: `${color}15`,
      }]}
      layout={{
        height, margin: { t: 5, r: 5, b: 5, l: 5 },
        paper_bgcolor: "transparent", plot_bgcolor: "transparent",
        xaxis: { visible: false }, yaxis: { visible: false },
      }}
      config={{ displayModeBar: false, staticPlot: true, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
