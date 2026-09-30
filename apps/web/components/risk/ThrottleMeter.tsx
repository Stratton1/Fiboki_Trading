import type { Figure } from "@/lib/types";
import { FigureValue } from "../FigureValue";
import { ViewStateTag } from "../ui/ViewStateTag";

/**
 * The drawdown throttle as a stepped meter (Risk & Exposure v2).
 *
 * The throttle is a step function of drawdown in the portfolio construction
 * policy (portfolio/construction.py: full size, then two de-risk factors,
 * then PAUSE, then FLATTEN required). The API does not report it: no route
 * carries the step in force, its thresholds, or the factors. So the meter is
 * drawn in the ABSENT state: the steps are named, no step is marked, and each
 * threshold reads "not reported". Nothing here is inferred from the drawdown
 * figure, because which step that drawdown selects depends on thresholds the
 * workstation has not been told.
 *
 * `current` and `thresholds` are the shape this meter will take once the API
 * reports the throttle; until then callers pass nothing.
 */

const STEPS = [
  { key: "full", label: "×1.0", meaning: "full size" },
  { key: "derisk", label: "×0.6", meaning: "de-risk" },
  { key: "severe", label: "×0.3", meaning: "severe" },
  { key: "pause", label: "PAUSE", meaning: "no new risk" },
  { key: "flatten", label: "FLATTEN", meaning: "flatten required" },
] as const;

export function ThrottleMeter({
  drawdown,
  current = null,
  thresholds = null,
}: {
  /** The drawdown figure as the API sent it, shown for context; it selects nothing. */
  drawdown: Figure | null;
  /** Index into the steps of the step in force, when the API reports it. */
  current?: number | null;
  /** The drawdown each step starts at (one per step after the first), when reported. */
  thresholds?: (Figure | null)[] | null;
}) {
  const reported = current !== null;
  return (
    <section
      className="throttle"
      data-testid="throttle-meter"
      data-state={reported ? "reported" : "absent"}
      aria-labelledby="throttle-title"
    >
      <div className="throttle__head">
        <h3 id="throttle-title" className="throttle__title">
          Drawdown throttle
        </h3>
        {reported ? null : <ViewStateTag state="absent">NOT REPORTED</ViewStateTag>}
      </div>
      <ol className="throttle__steps" aria-label="Throttle steps, least to most restrictive">
        {STEPS.map((step, index) => {
          const from = index === 0 ? null : (thresholds?.[index - 1] ?? null);
          const active = reported && current === index;
          return (
            <li
              key={step.key}
              className="throttle__step"
              data-step={step.key}
              data-active={active}
              data-testid="throttle-step"
              aria-current={active ? "step" : undefined}
            >
              <span className="throttle__label">{step.label}</span>
              <span className="throttle__meaning">{step.meaning}</span>
              <span className="throttle__from">
                {index === 0 ? (
                  "from 0"
                ) : from ? (
                  <>
                    from <FigureValue figure={from} showChip={false} />
                  </>
                ) : (
                  "from: not reported"
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {reported ? null : (
        <p className="throttle__note" data-testid="throttle-absent">
          The API does not report the throttle: the step in force, where each step begins and its factor are unknown
          here, so no step is marked. Step names are the construction policy&apos;s, not a reading.
          {drawdown ? (
            <>
              {" "}
              Drawdown as the API reports it: <FigureValue figure={drawdown} />.
            </>
          ) : null}
        </p>
      )}
    </section>
  );
}
