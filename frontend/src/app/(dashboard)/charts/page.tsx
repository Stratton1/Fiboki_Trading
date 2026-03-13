"use client";

import MultiChartLayout from "@/components/charts/core/MultiChartLayout";
import { PageHeader } from "@/components/PageHeader";

export default function ChartsPage() {
  return (
    <div className="flex flex-col gap-4 h-full">
      <PageHeader
        title="Trading Charts"
        subtitle="Multi-instrument charting workspace"
      />
      <div className="flex-1 min-h-0">
        <MultiChartLayout />
      </div>
    </div>
  );
}
