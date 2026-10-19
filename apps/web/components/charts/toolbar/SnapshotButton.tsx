"use client";

import { Camera } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { ApiError, apiFetch } from "@/lib/api";
import type { SnapshotStampView } from "@/lib/chart-contract";
import type { Envelope } from "@/lib/types";

/**
 * Snapshot (Wave 2B): the chart as a PNG with the API's provenance stamp
 * (GET /api/chart/stamp/{symbol}) printed under it. The composition code is
 * fetched on the first click (components/charts/snapshot.ts).
 */
export function SnapshotButton({
  symbol,
  tf,
  datasetVersion,
  capture,
}: {
  symbol: string;
  tf: string;
  datasetVersion: string | null;
  /** The chart's own screenshot, or null when no chart is drawn. */
  capture: () => HTMLCanvasElement | null;
}) {
  const [state, setState] = useState<{ kind: "idle" | "busy" | "done" | "error"; text: string; file?: string }>({
    kind: "idle",
    text: "",
  });
  const take = async () => {
    const canvas = capture();
    if (!canvas) {
      setState({ kind: "error", text: "No chart is drawn to snapshot." });
      return;
    }
    setState({ kind: "busy", text: "Taking the snapshot…" });
    try {
      const env = await apiFetch<Envelope<SnapshotStampView>>(
        `/api/chart/stamp/${encodeURIComponent(symbol)}?timeframe=${tf}&dataset_version_id=${encodeURIComponent(
          datasetVersion ?? "",
        )}`,
      );
      const { composeSnapshot, snapshotName } = await import("../snapshot");
      const { blob } = await composeSnapshot(canvas, env.data.text);
      const file = snapshotName(symbol, tf, env.data.generated_at);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = file;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setState({ kind: "done", text: `Saved ${file}: ${env.data.text}`, file });
    } catch (error) {
      setState({
        kind: "error",
        text: error instanceof ApiError ? `Snapshot not taken: ${error.message}` : "Snapshot not taken.",
      });
    }
  };
  return (
    <>
      <Button
        size="sm"
        data-testid="chart-snapshot"
        disabled={state.kind === "busy"}
        title="Snapshot: the chart as a PNG with its provenance stamp (symbol, timeframe, dataset, build, UTC time)"
        onClick={() => void take()}
      >
        <Camera size={13} aria-hidden="true" />
        PNG
      </Button>
      <span
        className="sr-only"
        role={state.kind === "error" ? "alert" : "status"}
        data-testid="snapshot-status"
        data-state={state.kind}
        data-file={state.file ?? ""}
      >
        {state.text}
      </span>
    </>
  );
}
