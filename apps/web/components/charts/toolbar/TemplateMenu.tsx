"use client";

import { LayoutTemplate, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { ApiError, apiFetch } from "@/lib/api";
import type { ChartTemplateBody, ChartTemplateRow, ChartTemplatesView } from "@/lib/chart-contract";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";

/**
 * Chart templates (programme feature 12), saved per operator on the server
 * (GET/PUT/DELETE /api/chart/templates). A template is display choices only
 * (layers, indicator toggles, which strategies' indicators to compute, which
 * trades to draw); applying one to another symbol changes what is drawn,
 * never a price. Saves are versioned: a save from a stale copy is refused
 * by the server (409) and the newer list is shown instead.
 */

const NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,47}$/;

export function TemplateMenu({
  current,
  onApply,
}: {
  /** What the chart shows now, as a template body (what "Save" stores). */
  current: ChartTemplateBody;
  onApply: (name: string, template: ChartTemplateBody) => void;
}) {
  const read = useApi<Envelope<ChartTemplatesView>>("/api/chart/templates");
  const [name, setName] = useState("");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const view = read.status === "success" ? read.data.data : null;
  const rows = view?.templates ?? [];
  const canEdit = view?.can_edit === true;

  const write = async (method: "PUT" | "DELETE", target: string, init: { body?: unknown; query?: string }) => {
    setBusy(true);
    setMessage(null);
    try {
      await apiFetch<Envelope<ChartTemplatesView>>(
        `/api/chart/templates/${encodeURIComponent(target)}${init.query ?? ""}`,
        { method, body: init.body === undefined ? undefined : JSON.stringify(init.body) },
      );
      setMessage({ kind: "ok", text: method === "PUT" ? `Saved “${target}”.` : `Deleted “${target}”.` });
    } catch (error) {
      const e = error instanceof ApiError ? error : null;
      setMessage({
        kind: "error",
        text: e ? `${e.message} (code ${e.code})` : "The template could not be saved.",
      });
    } finally {
      setBusy(false);
      read.reload();
    }
  };

  const save = () => {
    const trimmed = name.trim();
    if (!NAME.test(trimmed)) {
      setMessage({ kind: "error", text: "A name is 1 to 48 letters, digits, spaces, dots, dashes or underscores." });
      return;
    }
    const existing = rows.find((r) => r.name === trimmed);
    void write("PUT", trimmed, { body: { base_version: existing ? existing.version : 0, template: current } });
  };

  return (
    <Popover
      title="Chart templates"
      testId="template-popover"
      trigger={
        <Button size="sm" data-testid="chart-templates" title="Chart templates: saved layers and indicators, per operator">
          <LayoutTemplate size={13} aria-hidden="true" />
          Templates{view ? ` (${rows.length})` : ""}
        </Button>
      }
    >
      <div className="cw-tpl" data-testid="template-menu">
        {read.status === "loading" ? (
          <p className="muted" role="status">
            Reading your templates…
          </p>
        ) : read.status === "error" ? (
          <p className="cw__note cw__note--error" role="alert" data-testid="templates-error">
            Templates not loaded: {read.error.message} <span className="state__code">code: {read.error.code}</span>
          </p>
        ) : rows.length === 0 ? (
          <p className="muted" data-testid="templates-empty">
            No saved templates yet. Save what the chart shows now under a name; it applies to any symbol.
          </p>
        ) : (
          <ul className="cw-tpl__list">
            {rows.map((row: ChartTemplateRow) => (
              <li key={row.name} className="cw-tpl__row" data-testid="template-row" data-name={row.name}>
                <button
                  type="button"
                  className="cw-tpl__apply"
                  data-testid="template-apply"
                  onClick={() => {
                    onApply(row.name, row.template);
                    setMessage({ kind: "ok", text: `Applied “${row.name}”.` });
                  }}
                >
                  {row.name}
                </button>
                <span className="muted num">v{row.version}</span>
                {canEdit ? (
                  <button
                    type="button"
                    className="cw-dbar__btn"
                    aria-label={`Delete template ${row.name}`}
                    data-testid="template-delete"
                    disabled={busy}
                    onClick={() => void write("DELETE", row.name, { query: `?base_version=${row.version}` })}
                  >
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {canEdit ? (
          <form
            className="cw-tpl__save"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            <label htmlFor="cw-tpl-name" className="sr-only">
              Template name
            </label>
            <input
              id="cw-tpl-name"
              data-testid="template-name"
              value={name}
              maxLength={48}
              placeholder="Save current as…"
              onChange={(e) => setName(e.target.value)}
            />
            <Button size="sm" type="submit" disabled={busy || name.trim() === ""} data-testid="template-save">
              {rows.some((r) => r.name === name.trim()) ? "Overwrite" : "Save"}
            </Button>
          </form>
        ) : view ? (
          <p className="muted">Saving templates needs the operator role.</p>
        ) : null}
        {message ? (
          <p
            className={message.kind === "error" ? "cw__note cw__note--error" : "muted"}
            role={message.kind === "error" ? "alert" : "status"}
            data-testid="template-message"
          >
            {message.text}
          </p>
        ) : null}
      </div>
    </Popover>
  );
}
