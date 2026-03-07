"use client";
import dynamic from "next/dynamic";
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface DistributionProps {
  data: number[];
  title?: string;
  height?: number;
}

export function Distribution({ data, title = "PnL Distribution", height = 300 }: DistributionProps) {
  return (
    <Plot
      data={[
        {
          x: data,
          type: "histogram",
          marker: { color: "#16A34A" },
          nbinsx: 20,
        } as unknown as Plotly.Data,
      ]}
      layout={{
        title: { text: title, font: { size: 14, color: "#1C1917" } },
        height,
        margin: { t: 40, r: 20, b: 40, l: 50 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        xaxis: { showgrid: false, title: { text: "PnL" } },
        yaxis: { gridcolor: "#E5E5E5", title: { text: "Count" } },
        font: { family: "Inter, system-ui, sans-serif" },
        bargap: 0.05,
      }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%" }}
    />
  );
}
