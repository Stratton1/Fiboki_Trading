"use client";

import { useState } from "react";
import { useApi } from "@/lib/query";
import { AbsentNotice, UNWIRED_CODES } from "@/components/AbsentNotice";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { PageHead, SourceBadge, Tile } from "@/components/primitives";
import type { Envelope, Figure, ResearchMemoryView } from "@/lib/types";

interface RecallMatch {
  experiment_id: string;
  relation: string;
  outcome: string;
  created_at: string;
  similarity: Figure;
  why: string;
}

interface RecallView {
  novel: boolean;
  recommendation: string;
  matches: RecallMatch[];
  incomparable: { experiment_id: string; created_at: string }[];
}

/** INTELLIGENCE · Research Memory: "have we tried this already?" */
export default function ResearchMemoryPage() {
  const state = useApi<Envelope<ResearchMemoryView>>(
    "/api/intelligence/research-memory",
  );
  return (
    <>
      <PageHead
        title="Research Memory"
        intro="Answered before the work, not after it. Counts come from the experiment ledger. A folder of readings does not connect this page, and a missing rediscovery rate is not zero."
      />
      {state.status === "error" && UNWIRED_CODES.has(state.error.code) ? (
        <AbsentNotice title="Research memory is not wired" detail={state.error.message} />
      ) : (
      <AsyncBoundary state={state} label="research memory" onRetry={state.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            {envelope.data.available ? (
              <div className="tiles">
                <Tile label="Structures recorded" figure={envelope.data.structures_recorded} />
                <Tile label="Rediscovery rate" figure={envelope.data.rediscovery_rate} />
              </div>
            ) : (
              <AbsentNotice title="Research memory is absent" detail={envelope.data.detail} />
            )}
            <RecallBox />
          </>
        )}
      </AsyncBoundary>
      )}
    </>
  );
}

function RecallBox() {
  const [draft, setDraft] = useState("");
  const [submitted, setSubmitted] = useState("");
  const path = submitted
    ? `/api/intelligence/research-memory/recall?q=${encodeURIComponent(submitted)}`
    : null;
  const recall = useApi<Envelope<RecallView>>(path);
  return (
    <form
      className="recall"
      data-testid="research-recall"
      onSubmit={(event) => {
        event.preventDefault();
        setSubmitted(draft.trim());
      }}
    >
      <label htmlFor="research-recall-q">Ask the ledger</label>
      <input
        id="research-recall-q"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="Describe the idea before running it"
      />
      <button type="submit">Recall</button>
      {submitted && recall.status === "error" ? (
        <p role="alert">{recall.error.message}</p>
      ) : null}
      {recall.status === "success" ? (
        <div data-testid="research-recall-result">
          <p>{recall.data.data.novel ? "Novel" : "Not novel"}</p>
          <p>{recall.data.data.recommendation}</p>
          {recall.data.data.incomparable.length > 0 ? (
            <p data-testid="research-recall-incomparable">
              {recall.data.data.incomparable.length} incomparable experiment(s). These are not novelty.
            </p>
          ) : null}
          <ul>
            {recall.data.data.matches.map((match) => (
              <li key={match.experiment_id}>
                {match.relation} {match.outcome} similarity {match.similarity.value ?? "no data"}
                {match.similarity.estimated ? " (estimated overlap)" : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </form>
  );
}
