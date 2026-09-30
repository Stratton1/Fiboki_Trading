"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { CaveatList, PageHead, SourceBadge, ToneBadge } from "@/components/primitives";
import { RungMeter } from "@/components/research/RungMeter";
import { ViewStateTag } from "@/components/ui/ViewStateTag";
import { formatTimestamp } from "@/lib/format";
import { useApi, type ApiHandle } from "@/lib/query";
import { verdictTone } from "@/lib/tones";
import type {
  Envelope,
  LifecycleEvaluationView,
  LifecycleStatusView,
  Page,
  StrategyRow,
  ValidationRow,
} from "@/lib/types";

/**
 * One strategy, addressed by its content hash (`/lifecycle/<hash>`).
 *
 * The research API cannot address a document by hash: GET
 * /api/research/strategies/{id} takes the strategy id, and the list carries
 * each document's content hash cut to 12 characters. The lifecycle API CAN
 * (GET /api/trading/lifecycle/strategies/{content_hash}), and its answer names
 * the strategy id. So the page:
 *
 *  1. reads the lifecycle status for the hash; when it answers, the strategy
 *     id comes from it, and the registered document's own hash is compared
 *     with the one in the link (a document edited since would differ, and the
 *     page says so rather than showing the new document as if it were the
 *     same strategy);
 *  2. otherwise finds the document in the research list whose hash and the
 *     link's hash are prefixes of one another, and only if exactly one does;
 *  3. shows the document header, its validation summary as a rung meter with
 *     the binding constraint, and the lifecycle state and latest evaluation.
 *
 * What the APIs do not send is said, not filled in: the validation summary is
 * keyed by strategy id and carries no content hash, and it has no per-gate
 * statuses. Promotion is on Candidates (linked, not duplicated); demotion is
 * an automated rule's act, with no route behind it.
 */

const STRATEGIES_PATH = "/api/research/strategies";
const VALIDATION_PATH = "/api/research/validation";

/** Two hashes name the same document when one is a prefix of the other (≥ 8 characters). */
export function hashesAgree(a: string, b: string): boolean {
  const x = a.trim().toLowerCase();
  const y = b.trim().toLowerCase();
  const [short, long] = x.length <= y.length ? [x, y] : [y, x];
  return short.length >= 8 && long.startsWith(short);
}

type Resolved =
  | { kind: "found"; doc: StrategyRow; hashAgrees: boolean; via: "lifecycle" | "hash" }
  | { kind: "none"; reason: string }
  | { kind: "ambiguous"; docs: StrategyRow[] };

function resolve(hash: string, docs: readonly StrategyRow[], status: LifecycleStatusView | null): Resolved {
  if (status) {
    const doc = docs.find((d) => d.strategy_id === status.strategy_id);
    if (doc) return { kind: "found", doc, hashAgrees: hashesAgree(doc.content_hash, hash), via: "lifecycle" };
    return {
      kind: "none",
      reason: `The lifecycle store names strategy ${status.strategy_id}, but no registered document has that id.`,
    };
  }
  const matches = docs.filter((d) => hashesAgree(d.content_hash, hash));
  if (matches.length === 1 && matches[0]) return { kind: "found", doc: matches[0], hashAgrees: true, via: "hash" };
  if (matches.length > 1) return { kind: "ambiguous", docs: matches };
  return {
    kind: "none",
    reason:
      "No registered strategy document has this content hash. The research API addresses a document by strategy id, not by hash, and its list carries 12-character hashes; none of them and this hash are prefixes of one another.",
  };
}

export function LifecycleEntity({ hash }: { hash: string }) {
  const statusPath = `/api/trading/lifecycle/strategies/${encodeURIComponent(hash)}`;
  const status = useApi<Envelope<LifecycleStatusView>>(statusPath);
  const strategies = useApi<Page<StrategyRow>>(STRATEGIES_PATH);
  const validation = useApi<Page<ValidationRow>>(VALIDATION_PATH);
  const statusView = status.status === "success" ? status.data.data : null;
  // A never-evaluated strategy answers 404 by design ("nothing has looked"):
  // ask only when the status says it has been evaluated.
  const evaluation = useApi<Envelope<LifecycleEvaluationView>>(
    statusView?.ever_evaluated ? `${statusPath}/evaluation` : null,
  );

  const resolved =
    strategies.status === "success" && status.status !== "loading"
      ? resolve(hash, strategies.data.items, statusView)
      : null;
  const doc = resolved?.kind === "found" ? resolved.doc : null;
  const strategyId = doc?.strategy_id ?? statusView?.strategy_id ?? null;

  return (
    <>
      <nav className="entity-nav" aria-label="Back">
        <Link href="/" data-testid="lifecycle-back-command">
          <ArrowLeft size={13} aria-hidden="true" /> Command
        </Link>
      </nav>
      <PageHead
        title="Strategy"
        intro="One strategy by content hash: its declaration, what the promotion ladder concluded about it, and where it stands in the lifecycle."
      >
        <p className="mono muted" data-testid="lifecycle-hash">
          content hash {hash}
        </p>
      </PageHead>

      <section className="card" aria-labelledby="lifecycle-doc">
        <h2 id="lifecycle-doc" className="card__title">
          Strategy document
        </h2>
        <AsyncBoundary state={strategies} label="the strategy registry" onRetry={strategies.reload}>
          {(page) =>
            resolved === null ? (
              <p className="muted" role="status">
                Reading the lifecycle status…
              </p>
            ) : resolved.kind === "found" ? (
              <DocumentHeader page={page} doc={resolved.doc} hash={hash} hashAgrees={resolved.hashAgrees} />
            ) : resolved.kind === "ambiguous" ? (
              <div className="state state--error" data-testid="lifecycle-doc-ambiguous" role="alert">
                {resolved.docs.length} registered documents match this hash (
                {resolved.docs.map((d) => d.strategy_id).join(", ")}); the page will not guess which one is meant.{" "}
                <Link href="/research/strategies">Open the Strategies list</Link>.
              </div>
            ) : (
              <div className="state state--empty" data-testid="lifecycle-doc-none" role="status">
                <div className="state__title">No document for this hash</div>
                <div className="state__body">
                  {resolved.reason} Look for <span className="mono">{hash.slice(0, 12)}</span> in the Content hash column of{" "}
                  <Link href="/research/strategies" data-testid="lifecycle-strategies-link">
                    the Strategies list
                  </Link>
                  .
                </div>
              </div>
            )
          }
        </AsyncBoundary>
      </section>

      <section className="card" aria-labelledby="lifecycle-validation">
        <h2 id="lifecycle-validation" className="card__title">
          Latest validation report
        </h2>
        <AsyncBoundary state={validation} label="validation reports" onRetry={validation.reload}>
          {(page) => {
            const row = strategyId ? page.items.find((r) => r.strategy_id === strategyId) : undefined;
            if (!strategyId) {
              return (
                <p className="muted" data-testid="lifecycle-validation-unknown">
                  No strategy id is known for this hash, so no validation report can be matched to it.
                </p>
              );
            }
            if (!row) {
              return (
                <p className="muted" data-testid="lifecycle-validation-missing">
                  The research API lists no validation row for <span className="mono">{strategyId}</span>.
                </p>
              );
            }
            return <ValidationSummaryView page={page} row={row} />;
          }}
        </AsyncBoundary>
      </section>

      <section className="card" aria-labelledby="lifecycle-state">
        <h2 id="lifecycle-state" className="card__title">
          Lifecycle
        </h2>
        <AsyncBoundary state={status} label="the lifecycle status" onRetry={status.reload}>
          {(envelope) => <LifecycleStatus envelope={envelope} evaluation={evaluation} />}
        </AsyncBoundary>
        <p className="entity-links" data-testid="lifecycle-actions">
          {strategyId ? (
            <Link href={`/trading/candidates?row=${encodeURIComponent(strategyId)}`} data-testid="lifecycle-promote-link">
              Promote from Candidates
            </Link>
          ) : (
            <Link href="/trading/candidates" data-testid="lifecycle-promote-link">
              Candidates
            </Link>
          )}
          <span className="muted">
            Promotion is a named operator&apos;s act on Candidates, with the platform&apos;s preflight. Demotion is an
            automated rule&apos;s act on the worker&apos;s timer; no screen and no route demotes (routers/lifecycle.py).
          </span>
        </p>
      </section>
    </>
  );
}

function DocumentHeader({
  page,
  doc,
  hash,
  hashAgrees,
}: {
  page: Page<StrategyRow>;
  doc: StrategyRow;
  hash: string;
  hashAgrees: boolean;
}) {
  return (
    <div data-testid="lifecycle-doc" data-strategy-id={doc.strategy_id} data-hash-agrees={hashAgrees}>
      <SourceBadge source={page.source} />
      <CaveatList caveats={page.caveats} />
      {!hashAgrees ? (
        <p className="state state--error" role="alert" data-testid="lifecycle-hash-mismatch">
          The registered document for <span className="mono">{doc.strategy_id}</span> now has content hash{" "}
          <span className="mono">{doc.content_hash}</span>, not <span className="mono">{hash}</span>: it has been edited
          since the lifecycle record was made. The document below is the current one, not the one this record is about.
        </p>
      ) : null}
      <div className="entity-head">
        <h3 className="entity-head__title" data-testid="lifecycle-doc-name">
          {doc.name}
        </h3>
        <span className="badge badge--neutral">{doc.family}</span>
      </div>
      <p className="entity-hypothesis" data-testid="lifecycle-hypothesis">
        {doc.hypothesis || "No hypothesis is declared."}
      </p>
      <dl className="entity-facts">
        <div>
          <dt>Strategy id</dt>
          <dd className="mono">{doc.strategy_id}</dd>
        </div>
        <div>
          <dt>Content hash</dt>
          <dd className="mono">{doc.content_hash}</dd>
        </div>
        <div>
          <dt>Author</dt>
          <dd>{doc.author || "—"}</dd>
        </div>
        <div>
          <dt>Timeframes</dt>
          <dd>{doc.timeframes.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Universe</dt>
          <dd>{doc.universe.length} instrument(s)</dd>
        </div>
        <div>
          <dt>Rules</dt>
          <dd>
            <FigureValue figure={doc.rule_count} />
          </dd>
        </div>
        <div>
          <dt>Parameters</dt>
          <dd>
            <FigureValue figure={doc.parameter_count} />
          </dd>
        </div>
        <div>
          <dt>Complexity</dt>
          <dd>
            <FigureValue figure={doc.complexity} />
          </dd>
        </div>
        <div>
          <dt>Schema</dt>
          <dd className="mono">{doc.schema_version || "—"}</dd>
        </div>
        <div>
          <dt>Parents</dt>
          <dd className="mono">{doc.parent_strategy_ids.join(", ") || "none"}</dd>
        </div>
      </dl>
      <p className="entity-links">
        <Link href="/research/strategies">Strategies list</Link>
        <Link href={`/research/parameter-lab?strategy=${encodeURIComponent(doc.strategy_id)}`}>Parameter Lab</Link>
      </p>
    </div>
  );
}

function ValidationSummaryView({ page, row }: { page: Page<ValidationRow>; row: ValidationRow }) {
  return (
    <div data-testid="lifecycle-validation" data-verdict={row.verdict}>
      <SourceBadge source={page.source} />
      <div className="entity-head">
        <ToneBadge tone={verdictTone(row.verdict)} testId="verdict-badge" value={row.verdict} />
        <RungMeter row={row} />
      </div>
      <dl className="entity-facts">
        <div>
          <dt>Binding constraint</dt>
          <dd data-testid="lifecycle-binding">{row.binding_constraint || "—"}</dd>
        </div>
        <div>
          <dt>Gate set</dt>
          <dd className="mono">{row.gate_set_version || "—"}</dd>
        </div>
        <div>
          <dt>Dataset</dt>
          <dd className="mono">{row.dataset_version_id || "—"}</dd>
        </div>
        <div>
          <dt>Report version</dt>
          <dd className="mono">{row.report_version || "—"}</dd>
        </div>
      </dl>
      <p className="muted">{row.detail}</p>
      <p className="muted" data-testid="lifecycle-gates-note">
        Gate statuses: the research API sends the binding constraint only, not each gate&apos;s status, and it keys the
        report by strategy id without the content hash it was run on. The full gate table is in the report file
        (research/reports/<span className="mono">{row.strategy_id}</span>.json).
      </p>
    </div>
  );
}

function LifecycleStatus({
  envelope,
  evaluation,
}: {
  envelope: Envelope<LifecycleStatusView>;
  evaluation: ApiHandle<Envelope<LifecycleEvaluationView>>;
}) {
  const view = envelope.data;
  return (
    <div data-testid="lifecycle-status" data-lifecycle={view.lifecycle} data-evaluated={view.ever_evaluated}>
      <SourceBadge source={envelope.source} />
      <CaveatList caveats={envelope.caveats} />
      <div className="entity-head">
        <span className="badge badge--neutral" data-testid="lifecycle-state">
          {view.lifecycle.toUpperCase()}
        </span>
        <span className="muted">band {view.band}</span>
        {view.degraded ? <span className="badge badge--degraded">DEGRADED</span> : null}
      </div>
      <dl className="entity-facts">
        <div>
          <dt>In this state since</dt>
          <dd>{formatTimestamp(view.entered_state_at)}</dd>
        </div>
        <div>
          <dt>Last evaluated</dt>
          <dd>{view.last_evaluated_at ? formatTimestamp(view.last_evaluated_at) : "never"}</dd>
        </div>
        <div>
          <dt>Health</dt>
          <dd data-testid="lifecycle-health">
            {view.ever_evaluated ? (
              <FigureValue figure={view.health} />
            ) : (
              <ViewStateTag state="absent">NOT EVALUATED</ViewStateTag>
            )}
          </dd>
        </div>
        <div>
          <dt>Last degradation score</dt>
          <dd>
            <FigureValue figure={view.last_score} />
          </dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            <FigureValue figure={view.last_confidence} />
          </dd>
        </div>
        <div>
          <dt>Transitions</dt>
          <dd>
            <FigureValue figure={view.n_transitions} />
          </dd>
        </div>
        <div>
          <dt>Latched halts</dt>
          <dd>{view.latched_halts.join(", ") || "none"}</dd>
        </div>
        <div>
          <dt>Missing rule registrations</dt>
          <dd>{view.missing_rule_registrations.join(", ") || "none"}</dd>
        </div>
      </dl>
      {view.ever_evaluated ? (
        <AsyncBoundary state={evaluation} label="the latest evaluation" onRetry={evaluation.reload}>
          {(env) => (
            <div data-testid="lifecycle-evaluation" data-demoted={env.data.demoted}>
              <p>
                Latest evaluation {formatTimestamp(env.data.at)}: {env.data.state_before} → {env.data.state_after}
                {env.data.demoted ? " (demoted)" : ""}. {env.data.summary}
              </p>
              {env.data.rule_evaluations.length > 0 ? (
                <ul className="entity-rules">
                  {env.data.rule_evaluations.map((rule) => (
                    <li key={`${rule.kind}:${rule.registration_id}`} data-fired={rule.fired}>
                      <span className="mono">{rule.kind}</span> {rule.fired ? "FIRED" : "not fired"} · statistic{" "}
                      <FigureValue figure={rule.statistic} showChip={false} /> · threshold{" "}
                      <FigureValue figure={rule.threshold} showChip={false} />
                      {rule.detail ? <span className="muted"> · {rule.detail}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          )}
        </AsyncBoundary>
      ) : (
        <p className="muted" data-testid="lifecycle-never-evaluated">
          The monitors have never evaluated this strategy. That is not the same as healthy: nothing has looked.
        </p>
      )}
    </div>
  );
}
