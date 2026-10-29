"use client";

import { AbsentNotice } from "@/components/AbsentNotice";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList, PageHead, SourceBadge } from "@/components/primitives";
import { ViewStateTag } from "@/components/ui/ViewStateTag";
import { useApi } from "@/lib/query";
import type { AgentRoleRow, AgentRunView, Page } from "@/lib/types";

/**
 * INTELLIGENCE · Agent Desk.
 *
 * Roles, what each may do, and the last recorded audit rows. "Can execute"
 * is always NO: no execution capability exists in fiboki.agents.capabilities
 * to be granted. The activity mark moves only while the ledger or the
 * research heartbeat says a step is unfinished.
 */
function shortDigest(digest: string | null): string {
  if (!digest) return "—";
  return digest.length > 16 ? `${digest.slice(0, 16)}…` : digest;
}

export default function AgentsPage() {
  const roles = useApi<Page<AgentRoleRow>>("/api/intelligence/agents");
  const runs = useApi<Page<AgentRunView>>("/api/intelligence/runs");
  const runsAbsent = runs.status === "success" && runs.data.source.kind === "absent";
  const newest = runs.status === "success" && runs.data.items.length > 0 ? runs.data.items[0] : null;

  return (
    <>
      <PageHead
        title="Agent Desk"
        intro="The specialist roles, exactly what each may do, and the runs the audit ledger has recorded. No role can place or size an order."
      />
      <p className="agent-desk__banner" data-testid="agent-desk-no-execute">
        <ViewStateTag state="absent">NO EXECUTION</ViewStateTag>
        No role holds an execution capability. The capability does not exist to be granted.
      </p>
      <section className="agent-desk__runs" data-testid="agent-desk-runs" aria-label="Last run">
        <h2>Last run</h2>
        {runs.status === "success" && runsAbsent ? (
          <AbsentNotice title="No agent run has been recorded" detail={runs.data.source.detail} />
        ) : (
          <AsyncBoundary state={runs} label="agent runs" onRetry={runs.reload} isEmpty={() => newest === null}>
            {() =>
              newest ? (
                <>
                  <p
                    className={`agent-activity agent-activity--${newest.activity}`}
                    data-testid="agent-activity"
                    data-state={newest.activity}
                  >
                    <span className="agent-activity__mark" data-testid="agent-activity-mark" aria-hidden="true" />
                    <span>
                      {newest.activity === "working" ? "Working" : "Idle"}
                      {newest.activity_label ? ` — ${newest.activity_label}` : ""}
                    </span>
                  </p>
                  <p className="agent-desk__protocol" data-testid="agent-acceptance">
                    {newest.acceptance_protocol}
                  </p>
                  <CandidateReviews />
                  <SourceBadge source={runs.status === "success" ? runs.data.source : { kind: "absent", detail: "", as_of: null }} />
                  {runs.status === "success" ? <CaveatList caveats={runs.data.caveats} /> : null}
                  <dl className="agent-desk__facts" data-testid="agent-desk-last-run">
                    <dt>When</dt>
                    <dd className="num">{newest.recorded_at}</dd>
                    <dt>Workflow</dt>
                    <dd>{newest.workflow ?? newest.workflow_reason ?? "—"}</dd>
                    <dt>Role</dt>
                    <dd>{newest.role}</dd>
                    <dt>Outcome</dt>
                    <dd>{newest.outcome}</dd>
                    <dt>Model</dt>
                    <dd>{newest.model_id ?? newest.model_id_reason ?? "—"}</dd>
                    <dt>Digest</dt>
                    <dd className="num" title={newest.model_digest ?? newest.model_digest_reason ?? ""}>
                      {newest.model_digest ? shortDigest(newest.model_digest) : (newest.model_digest_reason ?? "—")}
                    </dd>
                    <dt>Tier</dt>
                    <dd>
                      {newest.tier} ({newest.tier_source})
                    </dd>
                  </dl>
                </>
              ) : null
            }
          </AsyncBoundary>
        )}
      </section>
      <AsyncBoundary state={roles} label="agent roles" onRetry={roles.reload}>
        {(page) => (
          <>
            <SourceBadge source={page.source} />
            <ul className="agent-desk" data-testid="agent-desk">
              {page.items.map((role) => (
                <li key={role.role} className="agent-desk__card" data-testid="agent-desk-role">
                  <header>
                    <h2>{role.role}</h2>
                    <span className="badge badge--ok" data-testid="agent-can-execute">
                      Can execute: NO
                    </span>
                  </header>
                  <p>{role.purpose}</p>
                  <p className="mono">
                    {role.capabilities.length > 0 ? role.capabilities.join(", ") : "no capabilities"}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}

function CandidateReviews() {
  const state = useApi<Page<{ candidate_id: string; brief: string; status: string; can_execute: boolean; refusal: string }>>(
    "/api/intelligence/candidates",
  );
  if (state.status !== "success") return null;
  return (
    <section data-testid="agent-candidates">
      <h2>Candidates</h2>
      {state.data.items.length === 0 ? (
        <p>No agent-authored candidate is awaiting review. A candidate cannot execute.</p>
      ) : (
        <ul>
          {state.data.items.map((item) => (
            <li key={item.candidate_id}>
              {item.brief} — {item.status}. Can execute: {item.can_execute ? "YES" : "NO"}. {item.refusal}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
