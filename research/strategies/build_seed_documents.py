"""Generate the seed DSL documents into research/strategies/ as JSON."""
from __future__ import annotations

from pathlib import Path

from fiboki.core.enums import Timeframe
from fiboki.strategy.dsl import (
    EventRestriction,
    ParameterSpec,
    PositionManagement,
    RuleSet,
    SessionRestriction,
    StopModel,
    StrategyDocument,
    StrategyFamily,
    TakeProfitLeg,
    TradeDirection,
    TrailingModel,
)
from fiboki.strategy.primitives import (
    CrossoverRule,
    IndicatorOperand,
    IndicatorSpec,
    IndicatorVsIndicatorRule,
    IndicatorVsPriceRule,
    PriceOperand,
    RegimeGateRule,
    ThresholdRule,
)

OUT = Path("research/strategies")
CLOSE = PriceOperand(field="close")

FX_MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
TREND_UNIVERSE = (*FX_MAJORS, "EURJPY", "GBPJPY", "XAUUSD", "US500", "DE40")


def spec(name: str, **params) -> IndicatorSpec:
    return IndicatorSpec(indicator=name, params=params)


def op(s: IndicatorSpec, output: str = "", offset: int = 0) -> IndicatorOperand:
    return IndicatorOperand(spec=s, output=output, offset=offset)


ATR14 = spec("atr", period=14)
ADX14 = spec("adx", period=14)
ATR_OP = op(ATR14)
ADX_OP = op(ADX14)


# --------------------------------------------------------------- 1. Ichimoku

ICHI = spec("ichimoku")

ichimoku_trend = StrategyDocument(
    strategy_id="ichimoku_kumo_trend",
    name="Ichimoku Kumo Trend",
    family=StrategyFamily.ICHIMOKU,
    hypothesis=(
        "ECONOMIC STORY. The Ichimoku lines are, stripped of the folklore, a set of "
        "Donchian midpoints over 9/26/52 bars plus a 26-bar displacement. A close "
        "above both spans with tenkan above kijun is therefore a compact statement "
        "that price is above its 9-, 26- and 52-bar equilibrium and that the short "
        "equilibrium is rising. If time-series momentum exists in FX and index CFDs "
        "at the daily-to-4-hour horizon -- and Moskowitz, Ooi and Pedersen (2012) "
        "and Hurst, Ooi and Pedersen (2017) document it across 50+ futures over a "
        "century -- then this is one cheap, low-parameter way to express it, and the "
        "displacement gives a natural trailing reference in the kijun.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. The specific claim that Ichimoku adds "
        "value in FX has an explicitly NEGATIVE published result: Deng, Sakurai and "
        "Ueda (2021) test Ichimoku rules on major currency pairs and find no "
        "significant profitability once data snooping is accounted for. Ichimoku is "
        "also drawn from the same moving-average family as the rules in Coakley, "
        "Marzano and Nankervis (2016), which did NOT survive Step-SPA multiple-"
        "testing correction on FX. The honest prior on this strategy is therefore "
        "'expected edge approximately zero before costs, negative after spread'. It "
        "is included as a baseline: if the research pipeline cannot show this "
        "underperforming, the pipeline is broken. Any positive result here must "
        "clear a deliberately high bar and survive the holdout untouched."
    ),
    universe=TREND_UNIVERSE,
    timeframes=(Timeframe.H4, Timeframe.D1),
    direction=TradeDirection.BOTH,
    regime=(
        RegimeGateRule(metric=ADX_OP, min_value=20.0),
    ),
    setup=RuleSet(
        long=(
            IndicatorVsPriceRule(indicator=op(ICHI, "cloud_top"), comparator="<", price=CLOSE),
            IndicatorVsIndicatorRule(
                left=op(ICHI, "tenkan"), right=op(ICHI, "kijun"), comparator=">"
            ),
        ),
        short=(
            IndicatorVsPriceRule(indicator=op(ICHI, "cloud_bottom"), comparator=">", price=CLOSE),
            IndicatorVsIndicatorRule(
                left=op(ICHI, "tenkan"), right=op(ICHI, "kijun"), comparator="<"
            ),
        ),
    ),
    entry=RuleSet(
        long=(CrossoverRule(fast=op(ICHI, "tenkan"), slow=op(ICHI, "kijun"), direction="above"),),
        short=(CrossoverRule(fast=op(ICHI, "tenkan"), slow=op(ICHI, "kijun"), direction="below"),),
    ),
    confirmation=RuleSet(
        # The causal restatement of the chikou test: price above where it was
        # chikou_shift bars ago. NOT close.shift(-26), which is a future value.
        long=(ThresholdRule(operand=op(ICHI, "chikou_above_price"), comparator=">", value=0.5),),
        short=(ThresholdRule(operand=op(ICHI, "chikou_above_price"), comparator="<", value=0.5),),
    ),
    stop=StopModel(
        kind="indicator_level",
        value=1.0,
        level=op(ICHI, "kijun"),
        atr=ATR_OP,
        buffer_atr=0.5,
        min_distance_atr=0.5,
    ),
    take_profits=(
        TakeProfitLeg(kind="r_multiple", value=1.5, allocation=0.5, label="scale_1"),
        TakeProfitLeg(kind="r_multiple", value=3.0, allocation=0.5, label="scale_2"),
    ),
    trailing=TrailingModel(kind="atr_chandelier", value=3.0, activate_after_r=1.0, atr=ATR_OP),
    position_management=PositionManagement(
        max_concurrent_positions=1,
        move_stop_to_breakeven_at_r=1.0,
        allow_reversal_on_opposite_signal=True,
        cooldown_bars_after_exit=2,
    ),
    sessions=SessionRestriction(windows=((7, 20),), weekdays=(0, 1, 2, 3, 4)),
    events=EventRestriction(block_minutes_before=30, block_minutes_after=30,
                            blocked_event_tags=("nfp", "cpi", "central_bank_rate")),
    parameters={
        "tenkan_period": ParameterSpec(kind="int", default=9, min_value=5, max_value=15, step=1,
                                       description="Conversion line lookback"),
        "kijun_period": ParameterSpec(kind="int", default=26, min_value=17, max_value=40, step=1,
                                      description="Base line lookback"),
        "senkou_b_period": ParameterSpec(kind="int", default=52, min_value=34, max_value=78,
                                         step=2, description="Leading span B lookback"),
        "senkou_shift": ParameterSpec(kind="int", default=26, min_value=13, max_value=39, step=1,
                                      description="Cloud displacement, INDEPENDENT of chikou"),
        "adx_floor": ParameterSpec(kind="float", default=20.0, min_value=10.0, max_value=35.0,
                                   step=2.5, description="Trend-regime gate"),
        "stop_buffer_atr": ParameterSpec(kind="float", default=0.5, min_value=0.0, max_value=1.5,
                                         step=0.25, description="ATR buffer beyond the kijun"),
    },
    author="fiboki-v2-seed",
    notes="Baseline. Expected to underperform; retained as a negative control.",
)


# -------------------------------------------------------------- 2. Donchian

DON = spec("donchian", period=20)
EMA100 = spec("ema", period=100)
RVOL = spec("realised_volatility", period=20)

donchian_breakout = StrategyDocument(
    strategy_id="donchian_breakout_atr",
    name="Donchian Breakout with ATR Stop",
    family=StrategyFamily.BREAKOUT,
    hypothesis=(
        "ECONOMIC STORY. A breakout of the prior 20-bar range is the classic "
        "time-series momentum expression. The economic mechanism most often "
        "advanced is slow information diffusion plus flow effects from risk "
        "management and option hedging: positions are added as price extends, so a "
        "range break is followed by further movement in the same direction more "
        "often than a coin flip. Moskowitz, Ooi and Pedersen (2012) document 12-"
        "month time-series momentum across asset classes; Hurst, Ooi and Pedersen "
        "(2017) extend it back to 1880. The 100-bar EMA filter restricts entries to "
        "the side of the longer trend, which is a crude proxy for the same effect at "
        "a slower frequency, and the ATR stop makes risk stationary across "
        "instruments and volatility regimes.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. The channel-breakout family is exactly "
        "the family that fails multiple-testing correction on FX: Coakley, Marzano "
        "and Nankervis (2016) find that technical trading rules on currencies do not "
        "survive Step-SPA once the full universe of rules considered is priced in. "
        "Managed-futures trend returns have also decayed materially since 2009 "
        "(widely documented in practitioner replication work), so historical "
        "Sharpe from long samples is likely an overestimate of forward performance. "
        "And crucially, the '20-bar high' is computed from the PRIOR window "
        "(donchian_upper_prior) -- a channel that includes the current bar's own "
        "high can never be broken by that bar, which is the kind of detail that "
        "turns a flat strategy into a spectacular backtest and nothing else."
    ),
    universe=TREND_UNIVERSE,
    timeframes=(Timeframe.H4, Timeframe.D1),
    direction=TradeDirection.BOTH,
    regime=(
        RegimeGateRule(metric=op(RVOL), min_value=0.0015, max_value=0.05),
    ),
    setup=RuleSet(
        long=(IndicatorVsPriceRule(indicator=op(EMA100), comparator="<", price=CLOSE),),
        short=(IndicatorVsPriceRule(indicator=op(EMA100), comparator=">", price=CLOSE),),
    ),
    entry=RuleSet(
        long=(IndicatorVsPriceRule(indicator=op(DON, "upper_prior"), comparator="<", price=CLOSE),),
        short=(IndicatorVsPriceRule(indicator=op(DON, "lower_prior"), comparator=">", price=CLOSE),),
    ),
    stop=StopModel(kind="atr_multiple", value=2.0, atr=ATR_OP, min_distance_atr=0.5),
    take_profits=(),
    trailing=TrailingModel(kind="atr_chandelier", value=3.0, activate_after_r=0.0, atr=ATR_OP),
    position_management=PositionManagement(
        max_concurrent_positions=1,
        allow_reversal_on_opposite_signal=True,
        max_bars_in_trade=120,
        cooldown_bars_after_exit=1,
    ),
    sessions=None,
    events=EventRestriction(block_minutes_before=15, block_minutes_after=15,
                            blocked_event_tags=("nfp", "central_bank_rate")),
    parameters={
        "channel_period": ParameterSpec(kind="int", default=20, min_value=10, max_value=60,
                                        step=5, description="Donchian lookback"),
        "trend_ema": ParameterSpec(kind="int", default=100, min_value=50, max_value=200,
                                   step=25, description="Directional filter EMA"),
        "stop_atr_multiple": ParameterSpec(kind="float", default=2.0, min_value=1.0,
                                           max_value=4.0, step=0.5),
        "trail_atr_multiple": ParameterSpec(kind="float", default=3.0, min_value=1.5,
                                            max_value=6.0, step=0.5),
    },
    author="fiboki-v2-seed",
)


# ----------------------------------------------------------- 3. RSI reversion

RSI14 = spec("rsi", period=14)
BB20 = spec("bollinger", period=20, num_std=2.0)
DON60 = spec("donchian", period=60)

rsi_reversion = StrategyDocument(
    strategy_id="rsi_band_mean_reversion",
    name="RSI Band Mean Reversion",
    family=StrategyFamily.MEAN_REVERSION,
    hypothesis=(
        "ECONOMIC STORY. Short-horizon reversal is one of the better-evidenced "
        "anomalies, and it has a mechanism rather than just a backtest: liquidity "
        "provision. Nagel (2012) shows that returns to short-term reversal "
        "strategies are compensation for supplying liquidity when it is scarce, and "
        "that those returns scale with measures of funding stress. The trade is "
        "therefore specified as a RE-ENTRY, not a touch: price must close back "
        "INSIDE the lower Bollinger band having closed outside it, with RSI still "
        "depressed. Waiting for the re-cross matters a great deal in practice -- at "
        "the moment of the band break RSI is still falling and the imbalance is "
        "still being created, so entering there is buying into the flow rather than "
        "supplying liquidity to it. The ADX ceiling is the load-bearing condition: "
        "mean reversion applied during a trend is just a slow way to lose money. "
        "The time stop caps the cost of being wrong about which regime we are in.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. Nagel's premium is measured on US "
        "equities at daily frequency with essentially zero spread; the FX and CFD "
        "version pays a spread on every leg and the effect must clear it. RSI-"
        "threshold rules specifically belong to the oscillator family that Coakley, "
        "Marzano and Nankervis (2016) found does not survive Step-SPA correction on "
        "currencies. The strategy also carries a structural negative skew: many "
        "small wins into the band midline, occasional large losses when the regime "
        "classifier is wrong, which flatters Sharpe and hides tail risk. Rank this "
        "one on tail-aware statistics, not Sharpe, and treat anything below a 1.4 "
        "profit factor as noise."
    ),
    universe=(*FX_MAJORS, "EURGBP", "AUDNZD", "EURCHF"),
    timeframes=(Timeframe.H1, Timeframe.H4),
    direction=TradeDirection.BOTH,
    regime=(
        RegimeGateRule(metric=ADX_OP, max_value=25.0),
    ),
    setup=RuleSet(
        # The PREVIOUS bar closed outside the band: the imbalance exists.
        long=(
            IndicatorVsPriceRule(
                indicator=op(BB20, "lower", offset=1),
                comparator=">",
                price=PriceOperand(field="close", offset=1),
            ),
        ),
        short=(
            IndicatorVsPriceRule(
                indicator=op(BB20, "upper", offset=1),
                comparator="<",
                price=PriceOperand(field="close", offset=1),
            ),
        ),
    ),
    entry=RuleSet(
        # ... and THIS bar closes back inside it: the imbalance is correcting.
        long=(CrossoverRule(fast=CLOSE, slow=op(BB20, "lower"), direction="above"),),
        short=(CrossoverRule(fast=CLOSE, slow=op(BB20, "upper"), direction="below"),),
    ),
    confirmation=RuleSet(
        long=(ThresholdRule(operand=op(RSI14), comparator="<", value=40.0),),
        short=(ThresholdRule(operand=op(RSI14), comparator=">", value=60.0),),
    ),
    invalidation=RuleSet(
        # A band break that is ALSO a fresh 60-bar extreme is a regime break, not
        # a liquidity imbalance. Refuse to fade it. The veto window is deliberately
        # LONGER than the Bollinger window: a 20-bar channel break coincides with
        # nearly every lower-band close and would veto the entire strategy.
        long=(IndicatorVsPriceRule(indicator=op(DON60, "lower_prior"), comparator=">",
                                   price=CLOSE),),
        short=(IndicatorVsPriceRule(indicator=op(DON60, "upper_prior"), comparator="<",
                                    price=CLOSE),),
    ),
    stop=StopModel(kind="atr_multiple", value=1.5, atr=ATR_OP, min_distance_atr=0.5),
    take_profits=(
        TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.5, label="first_r"),
        TakeProfitLeg(kind="indicator_level", value=1.0, allocation=0.5,
                      level=op(BB20, "mid"), label="band_midline"),
    ),
    trailing=None,
    position_management=PositionManagement(
        max_concurrent_positions=2,
        max_bars_in_trade=24,
        cooldown_bars_after_exit=6,
    ),
    sessions=SessionRestriction(windows=((6, 21),), weekdays=(0, 1, 2, 3, 4)),
    events=EventRestriction(block_minutes_before=45, block_minutes_after=45,
                            blocked_event_tags=("nfp", "cpi", "central_bank_rate"),
                            avoid_rollover_hour=True),
    parameters={
        "rsi_period": ParameterSpec(kind="int", default=14, min_value=7, max_value=21, step=1),
        "rsi_ceiling": ParameterSpec(kind="float", default=40.0, min_value=25.0, max_value=55.0,
                                     step=2.5,
                                     description="Depressed-RSI confirmation (mirrored for shorts)"),
        "adx_ceiling": ParameterSpec(kind="float", default=25.0, min_value=12.0, max_value=35.0,
                                     step=2.0, description="Range-regime gate"),
        "bb_num_std": ParameterSpec(kind="float", default=2.0, min_value=1.5, max_value=3.0,
                                    step=0.25),
        "time_stop_bars": ParameterSpec(kind="int", default=24, min_value=8, max_value=72, step=4),
        "breakout_veto_period": ParameterSpec(kind="int", default=60, min_value=30,
                                              max_value=120, step=10,
                                              description="Channel whose break vetoes the fade"),
    },
    author="fiboki-v2-seed",
)


# ---------------------------------------------------------- 4. MACD/EMA trend

MACD_STD = spec("macd", fast=12, slow=26, signal=9)
EMA50 = spec("ema", period=50)
EMA200 = spec("ema", period=200)

macd_ema_hybrid = StrategyDocument(
    strategy_id="macd_ema_trend_hybrid",
    name="MACD / EMA Trend Hybrid",
    family=StrategyFamily.TREND_FOLLOWING,
    hypothesis=(
        "ECONOMIC STORY. This separates the two jobs a trend system has to do. The "
        "50/200 EMA relationship is the slow state variable -- it answers 'which way "
        "is this market drifting over months' -- while the MACD line crossing its "
        "signal is the fast timing trigger that answers 'is momentum re-accelerating "
        "now'. Taking the fast trigger only in the direction of the slow state is a "
        "standard way to cut the whipsaw rate of a bare crossover roughly in half "
        "without giving up the large moves, which is where all the money in "
        "time-series momentum is made (Moskowitz, Ooi and Pedersen 2012). The MACD "
        "histogram is a difference of two EMAs, so this is an intentionally "
        "low-parameter expression of one idea rather than two ideas bolted together.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. This is the single worst-supported family "
        "in the academic literature once snooping is priced in. Moving-average "
        "crossover rules -- exactly these -- did NOT survive Step-SPA correction in "
        "Coakley, Marzano and Nankervis (2016) on FX. Earlier positive results such "
        "as Brock, Lakonishok and LeBaron (1992) are the canonical example of "
        "results that shrank when White's Reality Check was applied (Sullivan, "
        "Timmermann and White 1999). The 200-EMA filter also costs roughly 600 bars "
        "of warmup, which removes a real slice of any sample and makes the effective "
        "test period shorter than it looks. Expect a positive but small gross edge "
        "in trending instruments, and expect spread and slippage to consume most of "
        "it on anything faster than H4."
    ),
    universe=TREND_UNIVERSE,
    timeframes=(Timeframe.H4, Timeframe.D1),
    direction=TradeDirection.BOTH,
    regime=(
        RegimeGateRule(metric=ADX_OP, min_value=18.0),
    ),
    setup=RuleSet(
        long=(
            IndicatorVsIndicatorRule(left=op(EMA50), right=op(EMA200), comparator=">"),
            IndicatorVsPriceRule(indicator=op(EMA50), comparator="<", price=CLOSE),
        ),
        short=(
            IndicatorVsIndicatorRule(left=op(EMA50), right=op(EMA200), comparator="<"),
            IndicatorVsPriceRule(indicator=op(EMA50), comparator=">", price=CLOSE),
        ),
    ),
    entry=RuleSet(
        long=(
            CrossoverRule(fast=op(MACD_STD, "line"), slow=op(MACD_STD, "signal"),
                          direction="above"),
        ),
        short=(
            CrossoverRule(fast=op(MACD_STD, "line"), slow=op(MACD_STD, "signal"),
                          direction="below"),
        ),
    ),
    confirmation=RuleSet(
        long=(ThresholdRule(operand=op(MACD_STD, "line"), comparator="<", value=0.0),),
        short=(ThresholdRule(operand=op(MACD_STD, "line"), comparator=">", value=0.0),),
    ),
    stop=StopModel(kind="atr_multiple", value=2.5, atr=ATR_OP, min_distance_atr=0.5),
    take_profits=(
        TakeProfitLeg(kind="r_multiple", value=1.0, allocation=0.4, label="de_risk"),
        TakeProfitLeg(kind="r_multiple", value=2.5, allocation=0.3, label="core"),
    ),
    trailing=TrailingModel(kind="atr_chandelier", value=3.5, activate_after_r=1.0, atr=ATR_OP),
    position_management=PositionManagement(
        max_concurrent_positions=1,
        move_stop_to_breakeven_at_r=1.0,
        allow_reversal_on_opposite_signal=False,
        cooldown_bars_after_exit=3,
    ),
    sessions=SessionRestriction(windows=((6, 21),), weekdays=(0, 1, 2, 3, 4)),
    events=EventRestriction(block_minutes_before=30, block_minutes_after=30,
                            blocked_event_tags=("nfp", "cpi", "central_bank_rate")),
    parameters={
        "macd_fast": ParameterSpec(kind="int", default=12, min_value=6, max_value=20, step=1),
        "macd_slow": ParameterSpec(kind="int", default=26, min_value=18, max_value=40, step=2),
        "macd_signal": ParameterSpec(kind="int", default=9, min_value=5, max_value=15, step=1),
        "fast_ema": ParameterSpec(kind="int", default=50, min_value=20, max_value=100, step=10),
        "slow_ema": ParameterSpec(kind="int", default=200, min_value=100, max_value=300,
                                  step=25),
        "stop_atr_multiple": ParameterSpec(kind="float", default=2.5, min_value=1.0,
                                           max_value=4.0, step=0.5),
    },
    author="fiboki-v2-seed",
)


# ------------------------------------------------------------- 5. Fibonacci

FIB = spec("fibonacci", swing_lookback=5,
           retracements=(0.382, 0.5, 0.618, 0.786),
           extensions=(1.272, 1.618))

fib_pullback = StrategyDocument(
    strategy_id="fib_golden_pocket_pullback",
    name="Fibonacci Golden-Pocket Trend Pullback",
    family=StrategyFamily.FIBONACCI,
    hypothesis=(
        "ECONOMIC STORY -- AND IT IS A WEAK ONE. The defensible part of this "
        "strategy is not Fibonacci at all: it is 'buy a shallow pullback inside an "
        "established trend', which is a momentum-continuation trade with a decent "
        "prior. The 0.618-0.786 zone is simply a rule for defining 'shallow but not "
        "trivial', and the confirmed swing low that anchors it doubles as a natural, "
        "structural stop level, which makes the risk definition objective rather "
        "than arbitrary. To the extent this works it works because of trend "
        "continuation and because a cluster of stop orders sits under a prior swing, "
        "creating a real liquidity feature at that price.\n\n"
        "EVIDENCE AGAINST, STATED PLAINLY. There is no credible academic evidence "
        "that Fibonacci ratios have predictive power in financial prices. The "
        "numbers have no theoretical basis in market microstructure or asset "
        "pricing; the ratios are imported from a growth sequence with no connection "
        "to price formation, and studies that test them tend to find that any "
        "apparent effect is explained by ordinary support/resistance at prior swing "
        "levels -- which is to say the SWING matters and the RATIO does not. This "
        "strategy is therefore included to be falsified: the correct experiment is "
        "to compare it against the identical system using arbitrary retracement "
        "depths (e.g. 0.55-0.72). If the Fibonacci version does not beat the "
        "arbitrary version out of sample, the ratios add nothing and the family "
        "should be retired rather than tuned. Note also that V1's Fibonacci code "
        "was direction-blind and emitted nothing whenever the last swing low sat "
        "above the last swing high, so any V1 backtest of this family measured a "
        "broken implementation, not the idea."
    ),
    universe=TREND_UNIVERSE,
    timeframes=(Timeframe.H1, Timeframe.H4, Timeframe.D1),
    direction=TradeDirection.BOTH,
    regime=(
        RegimeGateRule(metric=ADX_OP, min_value=18.0),
    ),
    setup=RuleSet(
        long=(ThresholdRule(operand=op(FIB, "dir"), comparator=">", value=0.5),),
        short=(ThresholdRule(operand=op(FIB, "dir"), comparator="<", value=-0.5),),
    ),
    entry=RuleSet(
        # Price inside the 0.618-0.786 "golden pocket" of the confirmed leg.
        long=(
            IndicatorVsPriceRule(indicator=op(FIB, "ret_0618"), comparator=">=", price=CLOSE),
            IndicatorVsPriceRule(indicator=op(FIB, "ret_0786"), comparator="<=", price=CLOSE),
        ),
        short=(
            IndicatorVsPriceRule(indicator=op(FIB, "ret_0618"), comparator="<=", price=CLOSE),
            IndicatorVsPriceRule(indicator=op(FIB, "ret_0786"), comparator=">=", price=CLOSE),
        ),
    ),
    confirmation=RuleSet(
        long=(ThresholdRule(operand=op(RSI14), comparator=">", value=40.0),),
        short=(ThresholdRule(operand=op(RSI14), comparator="<", value=60.0),),
    ),
    stop=StopModel(
        kind="swing_structure",
        value=1.0,
        level=op(FIB, "start"),
        atr=ATR_OP,
        buffer_atr=0.25,
        min_distance_atr=0.5,
    ),
    take_profits=(
        TakeProfitLeg(kind="indicator_level", value=1.0, allocation=0.5,
                      level=op(FIB, "ext_1272"), label="ext_1272"),
        TakeProfitLeg(kind="indicator_level", value=1.0, allocation=0.5,
                      level=op(FIB, "ext_1618"), label="ext_1618"),
    ),
    trailing=TrailingModel(kind="breakeven_after_r", value=0.0, activate_after_r=1.0),
    position_management=PositionManagement(
        max_concurrent_positions=1,
        max_bars_in_trade=60,
        cooldown_bars_after_exit=4,
    ),
    sessions=SessionRestriction(windows=((7, 20),), weekdays=(0, 1, 2, 3, 4)),
    events=EventRestriction(block_minutes_before=30, block_minutes_after=30,
                            blocked_event_tags=("nfp", "cpi", "central_bank_rate")),
    parameters={
        "swing_lookback": ParameterSpec(kind="int", default=5, min_value=2, max_value=12, step=1,
                                        description="Fractal half-width; confirmation lag"),
        "pocket_near": ParameterSpec(kind="float", default=0.618, min_value=0.45, max_value=0.72,
                                     step=0.01, description="Shallow edge of the entry zone"),
        "pocket_far": ParameterSpec(kind="float", default=0.786, min_value=0.70, max_value=0.95,
                                    step=0.01, description="Deep edge of the entry zone"),
        "rsi_floor": ParameterSpec(kind="float", default=40.0, min_value=30.0, max_value=55.0,
                                   step=2.5),
        "stop_buffer_atr": ParameterSpec(kind="float", default=0.25, min_value=0.0,
                                         max_value=1.0, step=0.25),
    },
    author="fiboki-v2-seed",
    notes=("Falsification target: compare against identical rules with arbitrary "
           "(non-Fibonacci) retracement depths."),
)


DOCS = [ichimoku_trend, donchian_breakout, rsi_reversion, macd_ema_hybrid, fib_pullback]

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for doc in DOCS:
        path = OUT / f"{doc.strategy_id}.json"
        path.write_text(doc.to_json() + "\n")
        print(f"{doc.strategy_id:34s} hash={doc.short_hash} complexity={doc.complexity_score}")
    print(f"wrote {len(DOCS)} documents to {OUT}")
