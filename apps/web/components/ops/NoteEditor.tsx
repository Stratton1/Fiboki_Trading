"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type KeyboardEvent } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import {
  JOURNAL_NOTES_PATH,
  JOURNAL_VOCAB_PATH,
  NOTE_GRADES,
  NOTE_MAX,
  NOTE_REASON_MAX,
  journalNotePath,
  type JournalVocabularyView,
  type TradeNoteDetailView,
  type TradeNoteView,
} from "@/lib/ops";
import { invalidatePath, useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { useExecutionMode, useOperator } from "../shell/platform";
import { Button } from "../ui/Button";
import { Sheet } from "../ui/Sheet";

/**
 * The structured journal note of one trade (Wave 3O), keyboard first:
 *  - opened by Enter (or double-click) on a journal row;
 *  - 1 / 2 / 3 / 0 in the grade group set A / B / C / none; Tab moves on;
 *  - Ctrl+Enter (⌘+Enter) saves from any field; Esc closes without saving.
 * The API validates every field, keeps every version, and refuses a save
 * based on a stale version (409), which is shown rather than overwritten.
 * Annotations only: nothing trades on a note.
 */
export function NoteEditor({ tradeId, onClose }: { tradeId: string | null; onClose: () => void }) {
  return (
    <Sheet open={tradeId !== null} onOpenChange={(open) => (open ? undefined : onClose())} title={`Journal: ${tradeId ?? ""}`} testId="note-editor">
      {tradeId ? <NoteLoader tradeId={tradeId} onClose={onClose} /> : null}
    </Sheet>
  );
}

function NoteLoader({ tradeId, onClose }: { tradeId: string; onClose: () => void }) {
  const state = useApi<Envelope<TradeNoteDetailView>>(journalNotePath(tradeId));
  const vocab = useApi<Envelope<JournalVocabularyView>>(JOURNAL_VOCAB_PATH);
  return (
    <AsyncBoundary state={state} label="the journal note" onRetry={state.reload}>
      {(env) => (
        <NoteForm
          key={`${tradeId}-${env.data.note.version}`}
          note={env.data.note}
          history={env.data.history.length}
          suggestions={vocab.status === "success" ? vocab.data.data : null}
          onClose={onClose}
        />
      )}
    </AsyncBoundary>
  );
}

const csv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

function NoteForm({
  note,
  history,
  suggestions,
  onClose,
}: {
  note: TradeNoteView;
  history: number;
  suggestions: JournalVocabularyView | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const { mutationsAllowed } = useExecutionMode();
  const operator = useOperator();
  const ids = { note: useId(), tags: useId(), mistakes: useId(), url: useId(), reason: useId(), grade: useId() };
  const [text, setText] = useState(note.note);
  const [tags, setTags] = useState(note.tags.join(", "));
  const [grade, setGrade] = useState<string | null>(note.grade ?? null);
  const [mistakes, setMistakes] = useState(note.mistakes.join(", "));
  const [url, setUrl] = useState(note.screenshot_url ?? "");
  const [reason, setReason] = useState(note.intervention_reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Mirrors require_operator on PUT /api/trading/journal/notes/{id}; the server decides.
  const blocked = !mutationsAllowed
    ? "Saving is blocked: the execution mode is unknown or the workstation is disconnected."
    : operator !== null && operator.role === "viewer"
      ? "Saving is blocked: the operator role is required."
      : null;

  async function save() {
    if (blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(journalNotePath(note.trade_id), {
        method: "PUT",
        body: JSON.stringify({
          note: text,
          tags: csv(tags),
          grade,
          mistakes: csv(mistakes),
          screenshot_url: url.trim() || null,
          intervention_reason: reason.trim() || null,
          expected_version: note.version,
        }),
      });
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message} (${err.code})` : "The save failed.");
      setBusy(false);
      return;
    }
    await Promise.all([
      invalidatePath(client, journalNotePath(note.trade_id)),
      invalidatePath(client, JOURNAL_NOTES_PATH),
      invalidatePath(client, JOURNAL_VOCAB_PATH),
    ]);
    setBusy(false);
    onClose();
  }

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void save();
    }
  };
  const onGradeKey = (e: KeyboardEvent) => {
    const map: Record<string, string | null> = { "1": "A", "2": "B", "3": "C", "0": null };
    if (e.key in map) {
      e.preventDefault();
      setGrade(map[e.key] ?? null);
    }
  };

  return (
    <form
      className="ops-note-editor"
      data-testid="note-form"
      onKeyDown={onKey}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <p className="ops-hint">
        {note.version === 0 ? "No note yet." : `Version ${note.version} by ${note.updated_by}${note.updated_at ? ` at ${formatTimestamp(note.updated_at)}` : ""}; ${history} version(s) kept.`}{" "}
        Ctrl+Enter saves · Esc closes · 1/2/3/0 set the grade.
      </p>
      <fieldset className="ops-row" aria-describedby={ids.grade} onKeyDown={onGradeKey} data-testid="note-grade">
        <legend id={ids.grade} className="ops-subhead">
          Setup grade
        </legend>
        {[...NOTE_GRADES, null].map((g) => (
          <label key={g ?? "none"} className="ops-control">
            <input type="radio" name="grade" checked={grade === g} onChange={() => setGrade(g)} data-testid={`note-grade-${g ?? "none"}`} />
            {g ?? "none"}
          </label>
        ))}
      </fieldset>
      <label htmlFor={ids.note}>Note</label>
      <textarea id={ids.note} value={text} maxLength={NOTE_MAX} onChange={(e) => setText(e.target.value)} data-testid="note-text" autoFocus />
      <label htmlFor={ids.tags}>Tags (comma-separated)</label>
      <input id={ids.tags} value={tags} onChange={(e) => setTags(e.target.value)} data-testid="note-tags" list={`${ids.tags}-list`} />
      <datalist id={`${ids.tags}-list`}>
        {Object.keys(suggestions?.tags ?? {}).map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <label htmlFor={ids.mistakes}>Mistakes (comma-separated)</label>
      <input id={ids.mistakes} value={mistakes} onChange={(e) => setMistakes(e.target.value)} data-testid="note-mistakes" />
      {suggestions ? (
        <span className="ops-tags" aria-label="Suggested mistakes">
          {suggestions.suggested_mistakes.map((m) => (
            <button
              type="button"
              key={m}
              className="ops-tag linklike"
              onClick={() => setMistakes((cur) => (csv(cur).includes(m) ? cur : [...csv(cur), m].join(", ")))}
            >
              {m}
            </button>
          ))}
        </span>
      ) : null}
      <label htmlFor={ids.url}>Screenshot link</label>
      <input id={ids.url} type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" data-testid="note-url" />
      <label htmlFor={ids.reason}>Intervention reason (when you acted by hand)</label>
      <input id={ids.reason} value={reason} maxLength={NOTE_REASON_MAX} onChange={(e) => setReason(e.target.value)} data-testid="note-reason" />
      {error ? (
        <p className="state state--error" role="alert" data-testid="note-error">
          {error}
        </p>
      ) : null}
      {blocked ? (
        <p className="ack__blocked" data-testid="note-blocked">
          {blocked}
        </p>
      ) : null}
      <div className="ops-row">
        <Button type="submit" variant="primary" size="sm" disabled={blocked !== null || busy} data-testid="note-save">
          {busy ? "Saving…" : "Save note"}
        </Button>
        <Button type="button" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
