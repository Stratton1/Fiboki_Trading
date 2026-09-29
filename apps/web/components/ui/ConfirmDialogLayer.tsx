"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useId, useState } from "react";
import { Button } from "./Button";
import { DialogContent, DialogDescription, DialogTitle } from "./Dialog";
import { LIVE_CONFIRM_PHRASE, type ConfirmDialogProps } from "./ConfirmDialog";

/**
 * The Base UI half of ConfirmDialog (loaded on first open; see layers.ts).
 *
 * THE confirm dialog (v2, on Base UI Dialog). Every destructive or
 * capital-committing action goes through it.
 *
 * V1 used a native `confirm()` whose message was a hardcoded string that
 * stayed the same in every execution mode. Here:
 *
 *  - the consequence list comes from the API, per choice, per mode;
 *  - the current execution mode is stated in the dialog itself, with a DEMO
 *    stamp in demo; in LIVE a typed phrase is always required;
 *  - a reason is mandatory and goes to the audit trail;
 *  - choices are a radio group with no default when there are several;
 *  - server-supplied acknowledgements (realism caveats on a promotion) render
 *    as one required checkbox each, unticked on every open, and the ticked
 *    codes are handed to `onConfirm` so the caller sends what the operator
 *    actually acknowledged rather than a hard-coded `true`;
 *  - nothing can be confirmed while the execution mode is unknown.
 *
 * Base UI supplies the focus trap, the inert background and the return of
 * focus to the control that opened it. Escape and Cancel close it, except
 * while `busy`: a request in flight cannot be abandoned from here. A click on
 * the backdrop never closes it, so a stray click cannot discard a typed reason.
 */
export default function ConfirmDialogLayer({
  open,
  title,
  executionMode,
  choices,
  requireReason = true,
  reasonMinLength = 8,
  confirmPhrase,
  confirmLabel = "Confirm",
  busy = false,
  errorMessage,
  notice,
  acknowledgements = [],
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const [choiceId, setChoiceId] = useState<string | null>(
    choices.length === 1 ? (choices[0]?.id ?? null) : null,
  );
  const [reason, setReason] = useState("");
  const [phrase, setPhrase] = useState("");
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const id = useId();

  // Reset on open/close during render, not in an effect. A reason typed into
  // one confirmation must never survive into the next one: this dialog is
  // shared by every destructive action, and a stale reason would be written to
  // the audit trail against the wrong act.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    setChoiceId(choices.length === 1 ? (choices[0]?.id ?? null) : null);
    setReason("");
    setPhrase("");
    setTicked(new Set());
  }

  const mode = executionMode.toLowerCase();
  const modeKnown = mode !== "unknown" && mode !== "loading";
  const requiredPhrase = confirmPhrase ?? (mode === "live" ? LIVE_CONFIRM_PHRASE : undefined);

  // A lone choice is selected even when it arrives after the dialog opened
  // (server-computed choices load asynchronously).
  const selected =
    choices.find((c) => c.id === choiceId) ?? (choices.length === 1 ? (choices[0] ?? null) : null);
  const reasonOk = !requireReason || reason.trim().length >= reasonMinLength;
  const phraseOk = !requiredPhrase || phrase.trim() === requiredPhrase;
  const acknowledgedCodes = acknowledgements.map((a) => a.code).filter((code) => ticked.has(code));
  const acknowledgedOk = acknowledgedCodes.length === acknowledgements.length;
  const canConfirm =
    modeKnown && selected !== null && reasonOk && phraseOk && acknowledgedOk && !busy;

  const toggle = (code: string, on: boolean) =>
    setTicked((previous) => {
      const next = new Set(previous);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });

  return (
    <Base.Root
      open={open}
      disablePointerDismissal
      onOpenChange={(next, details) => {
        if (next) return;
        if (busy) {
          details.cancel();
          return;
        }
        onCancel();
      }}
    >
      <DialogContent testId="confirm-dialog" data-mode={mode} data-busy={busy}>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription className="dialog__mode" data-testid="confirm-mode">
          Execution mode: <strong>{executionMode.toUpperCase()}</strong>
          {mode === "demo" ? (
            <span className="dialog__stamp" aria-hidden="true">
              DEMO
            </span>
          ) : null}
        </DialogDescription>

        {!modeKnown ? (
          <p className="state state--error" data-testid="confirm-mode-unknown" role="alert">
            The execution mode could not be read from the platform, so this workstation cannot say
            where an order would go. Nothing can be confirmed until it can.
          </p>
        ) : null}

        {choices.length > 1 ? (
          <>
            <p className="field-label" id={`${id}-choices`}>
              Choose an action. There is no default.
            </p>
            <RadioGroup
              aria-labelledby={`${id}-choices`}
              value={choiceId}
              onValueChange={(value) => setChoiceId(value as string)}
              className="choice-group"
              data-testid="confirm-choices"
            >
              {choices.map((choice) => (
                <label
                  key={choice.id}
                  className="choice"
                  data-selected={choice.id === choiceId}
                  data-destructive={choice.destructive === true}
                  data-testid={`confirm-choice-${choice.id}`}
                >
                  <Radio.Root value={choice.id} className="choice__radio">
                    <Radio.Indicator className="choice__dot" />
                  </Radio.Root>
                  <span>
                    <span className="choice__title">{choice.title}</span>
                    <span className="choice__body">{choice.body}</span>
                  </span>
                </label>
              ))}
            </RadioGroup>
          </>
        ) : null}

        {selected ? (
          <>
            <p className="field-label">What this will do</p>
            <ul className="dialog__consequences" data-testid="confirm-consequences">
              {selected.consequences.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        ) : choices.length > 0 ? (
          <p className="muted" data-testid="confirm-no-choice">
            Select an action above to see its consequences.
          </p>
        ) : null}

        {acknowledgements.length > 0 ? (
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-1 mt-2.5 text-sm text-fg-muted">
              Acknowledge each caveat ({acknowledgedCodes.length} of {acknowledgements.length}).
              Every one is required.
            </legend>
            <ul className="ack-list" data-testid="confirm-acknowledgements">
              {acknowledgements.map((ack) => (
                <li key={ack.code}>
                  <label data-testid={`confirm-ack-${ack.code}`}>
                    <input
                      type="checkbox"
                      data-testid={`confirm-ack-input-${ack.code}`}
                      checked={ticked.has(ack.code)}
                      onChange={(e) => toggle(ack.code, e.target.checked)}
                    />
                    <span>
                      <span className="mono">{ack.code}</span> {ack.text}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        ) : null}

        {notice ? (
          <p className="muted" data-testid="confirm-notice" role="status">
            {notice}
          </p>
        ) : null}

        {requireReason ? (
          <>
            <label htmlFor={`${id}-reason`}>
              Reason (recorded to the audit trail, minimum {reasonMinLength} characters)
            </label>
            <textarea
              id={`${id}-reason`}
              data-testid="confirm-reason"
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </>
        ) : null}

        {requiredPhrase ? (
          <>
            <label htmlFor={`${id}-phrase`}>
              Type <span className="mono">{requiredPhrase}</span> to confirm
            </label>
            <input
              id={`${id}-phrase`}
              type="text"
              autoComplete="off"
              data-testid="confirm-phrase"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
            />
          </>
        ) : null}

        {errorMessage ? (
          <p className="state state--error mt-3" data-testid="confirm-error" role="alert">
            {errorMessage}
          </p>
        ) : null}

        <div className="dialog__actions">
          <Button onClick={onCancel} disabled={busy} data-testid="confirm-cancel">
            Cancel
          </Button>
          <Button
            variant={selected?.destructive ? "danger" : "primary"}
            disabled={!canConfirm}
            aria-busy={busy}
            data-testid="confirm-submit"
            onClick={() => {
              if (selected && canConfirm) {
                onConfirm(selected.id, reason.trim(), acknowledgedCodes);
              }
            }}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Base.Root>
  );
}
