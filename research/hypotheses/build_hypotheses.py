"""Write the campaign's hypothesis documents.

Kept as a script rather than as hand-written JSON for the same reason
``research/strategies/build_seed_documents.py`` is: the documents have to
validate, and a hand-edited JSON that fails ``Hypothesis``'s refutability check
is discovered at campaign time rather than at authoring time.

    python research/hypotheses/build_hypotheses.py

Each hypothesis states the mechanism, the falsifiable prediction, the exact
observation that would refute it, the regimes it claims, and the published
evidence AGAINST it. The evidence-against is taken from the seed documents'
own ``hypothesis`` prose, which already names it -- this just makes it a field
that a campaign can read rather than a paragraph a human has to.
"""
from __future__ import annotations

from pathlib import Path

from fiboki.core.enums import Timeframe
from fiboki.discovery.hypothesis import Evidence, Hypothesis

HERE = Path(__file__).resolve().parent

#: The refutation every hypothesis in this campaign shares, stated once.
LADDER_REFUTATION = (
    "Refuted, for this instrument and timeframe, if a candidate expressing it "
    "fails the production gate set: fewer than 400 trades at its declared "
    "defaults, or a walk-forward efficiency below 50 per cent, or a deflated "
    "Sharpe below 0.95 once the WHOLE campaign's trial count is priced in. Any "
    "one of those is an observation that counts against the claim on this cell; "
    "none of them refutes the mechanism in general."
)

MULTIPLE_TESTING_EVIDENCE = Evidence(
    direction="against",
    claim=(
        "Coakley, Marzano and Nankervis (2016) find that technical trading rules "
        "on currencies do not survive Step-SPA once the full universe of rules "
        "considered is priced in. The same correction is applied here, and the "
        "prior expectation is therefore that this fails."
    ),
    source="Journal of Banking and Finance 70, 'Estimating the economic value of "
    "technical trading rules'",
    strength="strong",
)


HYPOTHESES = (
    Hypothesis(
        hypothesis_id="gold_range_breakout_persists",
        title="Range breakouts on gold persist at H4 when volatility is not extreme",
        economic_rationale=(
            "Gold is held by central banks, ETFs, macro funds and retail, whose "
            "position changes are large relative to the turnover of any one "
            "four-hour bar and are worked over days rather than minutes. When a "
            "multi-day range breaks, the flow that broke it is typically not "
            "finished, and risk-management and option-hedging flows add to the "
            "move in the same direction. The claim is that this makes the "
            "conditional distribution of the next few bars after a range break "
            "asymmetric in the direction of the break, by more than the dealing "
            "cost of taking the position."
        ),
        prediction=(
            "A close beyond the prior 20-bar Donchian channel on XAUUSD H4, taken "
            "in the direction of a 100-bar EMA filter and managed with an ATR stop "
            "and an ATR chandelier trail, has positive expectancy net of an "
            "IG_REALISTIC spread across the 2009-2022 research window, and that "
            "expectancy survives fitting the channel and stop parameters on one "
            "window and trading them on the next."
        ),
        refutation=LADDER_REFUTATION,
        instruments=("XAUUSD",),
        timeframes=(Timeframe.H4,),
        regimes=(
            "realised volatility above a floor and below its top tail",
            "price on the far side of the 100-bar EMA",
        ),
        evidence=(
            Evidence(
                direction="for",
                claim=(
                    "Moskowitz, Ooi and Pedersen (2012) document time-series "
                    "momentum across 58 instruments including commodities; Hurst, "
                    "Ooi and Pedersen (2017) extend the result back to 1880."
                ),
                source="Journal of Financial Economics 104(2); Journal of Portfolio "
                "Management 44(1)",
                strength="moderate",
            ),
            MULTIPLE_TESTING_EVIDENCE,
            Evidence(
                direction="against",
                claim=(
                    "Managed-futures trend returns have decayed materially since "
                    "2009, which is most of this sample, so a Sharpe estimated over "
                    "the whole window is likely an overestimate of what the last "
                    "third of it would have paid."
                ),
                source="practitioner replication work, widely reported",
                strength="moderate",
            ),
        ),
        seed_strategy_ids=("donchian_breakout_atr",),
        author="phase-k",
        tags=("breakout", "trend", "xauusd"),
    ),
    Hypothesis(
        hypothesis_id="kumo_state_conditions_trend",
        title="Ichimoku cloud state marks the regime in which gold trends pay",
        economic_rationale=(
            "The Ichimoku cloud is a smoothed, forward-projected band of the "
            "midpoints of two lookbacks. Its economic content is not the geometry "
            "but the conditioning: price above a rising cloud is a compressed "
            "statement that several horizons of the midpoint agree on direction, "
            "which is the state in which the slow-diffusion argument for trend "
            "following is supposed to apply. The claim is therefore about the "
            "GATE, not the signal: trend entries conditioned on cloud state should "
            "do better than the same entries taken unconditionally."
        ),
        prediction=(
            "Tenkan/Kijun entries on XAUUSD H4, gated on price being on the correct "
            "side of the Kumo and on an ADX floor, have positive expectancy net of "
            "costs and transfer across walk-forward folds better than the same "
            "entries with the cloud gate removed."
        ),
        refutation=LADDER_REFUTATION,
        instruments=("XAUUSD",),
        timeframes=(Timeframe.H4,),
        regimes=("price outside the Kumo", "ADX above its floor"),
        evidence=(
            Evidence(
                direction="for",
                claim=(
                    "Conditioning trend entries on a slower trend state is the "
                    "standard construction in the managed-futures literature, and "
                    "is where most of the reported improvement over naive "
                    "crossovers comes from."
                ),
                strength="weak",
            ),
            MULTIPLE_TESTING_EVIDENCE,
            Evidence(
                direction="against",
                claim=(
                    "Ichimoku has no derivation and a large number of conventional "
                    "constants (9, 26, 52, 26-shift) that were chosen for a "
                    "six-day trading week in the 1960s. There is no reason those "
                    "constants should matter on a 24-hour H4 gold clock, and a "
                    "result that depends on them is a fit to the sample."
                ),
                strength="strong",
            ),
            Evidence(
                direction="against",
                claim=(
                    "The 2009-2025 XAUUSD H4 run of this seed produced 49 trades "
                    "and a NEGATIVE expectancy of -1.05 USD at its declared "
                    "defaults once the engine honoured its own rollover-hour "
                    "restriction."
                ),
                source="research/reports/xauusd_h4/README.md",
                strength="strong",
            ),
        ),
        seed_strategy_ids=("ichimoku_kumo_trend",),
        author="phase-k",
        tags=("ichimoku", "regime", "xauusd"),
    ),
    Hypothesis(
        hypothesis_id="macd_confirms_ema_trend",
        title="MACD turns confirm EMA-defined trend entries without arriving too late",
        economic_rationale=(
            "An EMA pair states the direction of the trend; a MACD histogram turn "
            "states that momentum within that trend has just re-accelerated. If "
            "the flow argument for trend persistence is right, the re-acceleration "
            "is the observable part of a new tranche of flow entering, and entering "
            "with it should be better than entering at an arbitrary point in the "
            "trend. The competing effect is that a confirmation always arrives "
            "late, so the entry is worse by the amount the price has already moved."
        ),
        prediction=(
            "On XAUUSD H4, EMA-trend entries confirmed by a MACD histogram turn "
            "have a walk-forward efficiency above 50 per cent -- that is, the "
            "MACD parameters selected on a train window keep more than half their "
            "in-sample profit rate on the following test window."
        ),
        refutation=LADDER_REFUTATION,
        instruments=("XAUUSD",),
        timeframes=(Timeframe.H4,),
        regimes=("ADX above its floor", "fast EMA on the correct side of the slow EMA"),
        evidence=(
            Evidence(
                direction="for",
                claim=(
                    "Momentum confirmation raises the hit rate of trend entries in "
                    "most published rule surveys, at the cost of a worse average "
                    "entry price."
                ),
                strength="weak",
            ),
            MULTIPLE_TESTING_EVIDENCE,
            Evidence(
                direction="against",
                claim=(
                    "The 2009-2025 XAUUSD H4 run of this seed produced 41 trades "
                    "and a walk-forward efficiency of 5.6 per cent against a 50 per "
                    "cent bar, which is the signature of a selection step fitting "
                    "noise rather than finding a stable optimum."
                ),
                source="research/reports/xauusd_h4/README.md",
                strength="strong",
            ),
        ),
        seed_strategy_ids=("macd_ema_trend_hybrid",),
        author="phase-k",
        tags=("trend", "momentum", "xauusd"),
    ),
    Hypothesis(
        hypothesis_id="golden_pocket_pullback_holds",
        title="Gold pullbacks into the 0.618-0.65 retracement resume the trend",
        economic_rationale=(
            "The claim is not about the number. It is that a large cohort of "
            "discretionary and systematic participants place resting orders at "
            "conventional retracement levels, so those levels become places where "
            "resting liquidity is concentrated. Concentrated resting liquidity "
            "makes a reaction more likely at that price than at a neighbouring one, "
            "which is a self-fulfilling mechanism rather than a geometric one -- "
            "and self-fulfilling mechanisms are real for as long as the convention "
            "holds."
        ),
        prediction=(
            "Entries on XAUUSD H4 taken when price retraces into the 0.618-0.65 "
            "band of the last completed swing, in the direction of the prevailing "
            "trend and with RSI not at an extreme, have positive expectancy net of "
            "an IG_REALISTIC spread."
        ),
        refutation=LADDER_REFUTATION,
        instruments=("XAUUSD",),
        timeframes=(Timeframe.H4,),
        regimes=("ADX above its floor", "an identified completed swing"),
        evidence=(
            Evidence(
                direction="for",
                claim=(
                    "Order-book studies find clustering of resting orders at round "
                    "numbers and at conventional levels, which is the mechanism this "
                    "claim depends on."
                ),
                strength="weak",
            ),
            MULTIPLE_TESTING_EVIDENCE,
            Evidence(
                direction="against",
                claim=(
                    "The 2009-2025 XAUUSD H4 run of this seed produced 231 trades "
                    "at an expectancy of -16.58 USD per trade. It is the worst of "
                    "the four seeds on this cell by a wide margin, and it was "
                    "already negative before the engine gained its full exit "
                    "vocabulary."
                ),
                source="research/reports/xauusd_h4/README.md",
                strength="strong",
            ),
            Evidence(
                direction="against",
                claim=(
                    "The specific 0.618 level has no derivation. If the mechanism "
                    "is concentrated resting liquidity, the band should be wide and "
                    "the result should be insensitive to its exact edges; if the "
                    "result depends on the exact edges, the mechanism is not the "
                    "one claimed."
                ),
                strength="moderate",
            ),
        ),
        seed_strategy_ids=("fib_golden_pocket_pullback",),
        author="phase-k",
        tags=("fibonacci", "mean_reversion", "xauusd"),
    ),
    Hypothesis(
        hypothesis_id="rsi_band_reversion_in_range",
        title="RSI band extremes revert while gold is ranging, not trending",
        economic_rationale=(
            "In the absence of new information, an inventory imbalance at a market "
            "maker pushes price away from the clearing level and is then worked "
            "back. That is a real, short-horizon mean-reversion mechanism, and it "
            "is the opposite of the trend mechanism -- so it should appear in the "
            "regimes where the trend mechanism does not, namely low ADX."
        ),
        prediction=(
            "Entries against an RSI band extreme, gated on ADX below a ceiling and "
            "vetoed by a recent range breakout, have positive expectancy net of "
            "costs on instruments where the strategy declares a universe."
        ),
        refutation=LADDER_REFUTATION
        + " It is additionally NOT TESTED on XAUUSD at all: the seed document "
        "does not declare gold in its universe, and running it there would be "
        "validating a strategy nobody wrote.",
        instruments=("EURUSD",),
        timeframes=(Timeframe.H1, Timeframe.H4),
        regimes=("ADX below its ceiling", "no recent range breakout"),
        evidence=(
            Evidence(
                direction="for",
                claim=(
                    "Short-horizon reversal is one of the few effects with a "
                    "market-microstructure derivation rather than a behavioural "
                    "story, and it is documented in FX at intraday horizons."
                ),
                strength="moderate",
            ),
            MULTIPLE_TESTING_EVIDENCE,
            Evidence(
                direction="against",
                claim=(
                    "Reversion strategies have a short left tail: they are right "
                    "most of the time and catastrophically wrong when the range "
                    "breaks, which makes an in-sample expectancy an especially bad "
                    "estimator of forward performance."
                ),
                strength="strong",
            ),
        ),
        seed_strategy_ids=("rsi_band_mean_reversion",),
        author="phase-k",
        tags=("mean_reversion", "rsi"),
    ),
)


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    for hypothesis in HYPOTHESES:
        path = HERE / f"{hypothesis.hypothesis_id}.json"
        path.write_text(hypothesis.to_json(), encoding="utf-8")
        print(f"wrote {path} [{hypothesis.short_hash}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
