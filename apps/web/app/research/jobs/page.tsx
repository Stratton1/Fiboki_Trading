"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList, PageHead, SourceBadge, ToneBadge } from "@/components/primitives";
import { ResearchNav } from "@/components/research/ResearchNav";
import { jobTone } from "@/components/research/tones";
import { formatAge, formatNumber, formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import { researchHref, type JobQueueView, type JobView, type ShardView } from "@/lib/research";
import type { Envelope, Figure } from "@/lib/types";

const ORDER = ["running", "pending", "failed", "dead_letter", "succeeded"];

/**
 * RESEARCH · Job queue: the research worker's durable job ledger
 * (`<state>/jobs.sqlite`) and the study shards under research/reports/*\/shards,
 * from GET /api/research/jobs, re-read every 10 seconds. Progress and ETAs
 * are the server's; an ETA is marked as an estimate.
 */
export default function JobsPage() {
  const state = useApi<Envelope<JobQueueView>>("/api/research/jobs", { refreshMs: 10_000 });
  return (
    <div className="rs-page">
      <ResearchNav />
      <PageHead
        title="Research job queue"
        intro="What the research worker is running, what is queued, what failed, and the long study shards on disk, with progress and an estimated finish. Outputs link to their tearsheets."
      />
      <AsyncBoundary state={state} label="the job queue" onRetry={state.reload}>
        {(env) => <Queue env={env} />}
      </AsyncBoundary>
    </div>
  );
}

function Queue({ env }: { env: Envelope<JobQueueView> }) {
  const v = env.data;
  const live = v.jobs.filter((j) => j.status !== "succeeded");
  const done = v.jobs.filter((j) => j.status === "succeeded").slice(0, 25);
  return (
    <>
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      <dl className="rs-stats" data-testid="job-counts">
        {ORDER.map((s) => (
          <div key={s} className="rs-stat" data-status={s}>
            <dt className="rs-stat__k">{jobTone(s).label}</dt>
            <dd data-testid={`count-${s}`}>{v.counts[s] ?? "—"}</dd>
          </div>
        ))}
      </dl>
      <section className="rs-panel" aria-labelledby="jobs-live">
        <h2 className="rs-panel__title" id="jobs-live">
          Running, queued and failed
        </h2>
        {live.length === 0 ? (
          <p className="muted" data-testid="jobs-idle">
            Nothing is running or queued. {v.job_ledger}
          </p>
        ) : (
          <JobTable jobs={live} testId="jobs-live" />
        )}
      </section>
      <section className="rs-panel" aria-labelledby="jobs-shards">
        <h2 className="rs-panel__title" id="jobs-shards">
          Study shards
        </h2>
        {v.shards.length === 0 ? (
          <p className="muted" data-testid="shards-none">
            No shard status files under <span className="mono">{v.shards_root}</span>. Studies run on the MacBook write
            them there (E-4, K6, E-1); none are present on this deployment.
          </p>
        ) : (
          <ShardTable shards={v.shards} />
        )}
      </section>
      <section className="rs-panel" aria-labelledby="jobs-done">
        <h2 className="rs-panel__title" id="jobs-done">
          Recently finished
        </h2>
        {done.length === 0 ? <p className="muted">No finished jobs in the ledger.</p> : <JobTable jobs={done} testId="jobs-done" />}
      </section>
      <p className="ax__note">{v.eta_method}</p>
    </>
  );
}

function Seconds({ figure }: { figure: Figure }) {
  if (figure.value === null) return <span className="muted">—</span>;
  return (
    <span className="mono" title={figure.estimated ? "estimate" : undefined}>
      {duration(figure.value)}
      {figure.estimated ? <sup className="muted"> est</sup> : null}
    </span>
  );
}

function duration(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86_400)}d ${Math.round((s % 86_400) / 3600)}h`;
}

function JobTable({ jobs, testId }: { jobs: JobView[]; testId: string }) {
  return (
    <div className="table-wrap">
      <table className="rs-table" data-testid={testId}>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Job</th>
            <th scope="col">Type</th>
            <th scope="col">Strategy</th>
            <th scope="col">Instrument</th>
            <th scope="col">Submitted</th>
            <th scope="col" className="num">
              Elapsed
            </th>
            <th scope="col" className="num">
              ETA
            </th>
            <th scope="col" className="num">
              Attempts
            </th>
            <th scope="col">Outputs</th>
            <th scope="col">Error</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.job_id} data-testid="job-row" data-status={j.status}>
              <td>
                <ToneBadge tone={jobTone(j.status)} testId="job-status" value={j.status} />
              </td>
              <td className="mono">{j.job_id}</td>
              <td>{j.job_type}</td>
              <td>{j.strategy_id ? <Link href={researchHref.strategy(j.strategy_id)}>{j.strategy_id}</Link> : "—"}</td>
              <td className="mono">{j.instrument || "—"}</td>
              <td>{formatTimestamp(j.submitted_at)}</td>
              <td className="num">
                <Seconds figure={j.elapsed_seconds} />
              </td>
              <td className="num" data-testid="job-eta">
                <Seconds figure={j.eta_seconds} />
              </td>
              <td className="num">
                {j.attempts}/{j.max_attempts}
              </td>
              <td>
                {Object.entries(j.outputs).length === 0
                  ? "—"
                  : Object.entries(j.outputs).map(([k, href]) => (
                      <Link key={k} href={href} className="rs-outlink">
                        {k}
                      </Link>
                    ))}
              </td>
              <td className="wrap">{j.error ? <span className="neg">{j.error.slice(0, 160)}</span> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Progress({ shard }: { shard: ShardView }) {
  const v = shard.progress.value;
  if (v === null) return <span className="muted">size not declared</span>;
  return (
    <svg className="rs-progress" viewBox="0 0 100 6" preserveAspectRatio="none" role="img" aria-label={`${formatNumber(v * 100, "pct")} done`} data-partial={String(shard.partial)}>
      <rect className="rs-progress__track" x="0" y="0" width="100" height="6" />
      <rect className="rs-progress__fill" x="0" y="0" width={Math.max(0, Math.min(100, v * 100))} height="6" />
    </svg>
  );
}

function ShardTable({ shards }: { shards: ShardView[] }) {
  return (
    <div className="table-wrap">
      <table className="rs-table" data-testid="shards">
        <thead>
          <tr>
            <th scope="col">Study</th>
            <th scope="col">Stage</th>
            <th scope="col">File</th>
            <th scope="col">Progress</th>
            <th scope="col" className="num">
              Done
            </th>
            <th scope="col" className="num">
              Per unit
            </th>
            <th scope="col" className="num">
              ETA
            </th>
            <th scope="col">Last written</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {shards.map((s) => (
            <tr key={s.path} data-testid="shard-row" data-study={s.study}>
              <td>{s.study}</td>
              <td>{s.stage || "—"}</td>
              <td className="mono wrap">{s.path}</td>
              <td>
                <Progress shard={s} />
              </td>
              <td className="num">
                {s.done}
                {s.total !== null ? ` / ${s.total}` : ""} {s.unit}
              </td>
              <td className="num">{s.seconds_per_unit.value === null ? "—" : duration(s.seconds_per_unit.value)}</td>
              <td className="num" data-testid="shard-eta">
                <Seconds figure={s.eta_seconds} />
              </td>
              <td>{formatAge(s.age_seconds.value)}</td>
              <td>
                {s.error ? (
                  <span className="badge badge--down">{s.error.toUpperCase()}</span>
                ) : s.partial ? (
                  <span className="badge badge--unknown">IN PROGRESS</span>
                ) : (
                  <span className="badge badge--ok">DONE</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
