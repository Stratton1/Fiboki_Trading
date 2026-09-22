"""Hand-calculated golden values. Never weaken one of these to make it pass.

V1 shipped its flagship indicator -- Ichimoku -- with **zero** pinned constants.
Any refactor could have silently moved the cloud and no test would have noticed.
Everything below is derived by hand, with the arithmetic written out, on one
small fixed series, and asserted exactly (or to 1e-12 where the true value is a
repeating fraction).

THE SERIES (20 bars). ``high = close + 1``, ``low = close - 1``,
``open`` = previous close. Chosen so that most intermediate quantities are exact
binary fractions and can be checked on paper.

    i:      0    1    2    3    4    5    6    7    8    9
    close: 100  101  102  103  104  103  102  103  105  107
    i:     10   11   12   13   14   15   16   17   18   19
    close: 106  105  106  108  110  109  108  109  111  113
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from fiboki.indicators import (
    ATR,
    EMA,
    MACD,
    RSI,
    SMA,
    WMA,
    Bollinger,
    Donchian,
    Fibonacci,
    Ichimoku,
    SwingDetector,
)

pytestmark = pytest.mark.golden

CLOSES = [
    100.0, 101.0, 102.0, 103.0, 104.0, 103.0, 102.0, 103.0, 105.0, 107.0,
    106.0, 105.0, 106.0, 108.0, 110.0, 109.0, 108.0, 109.0, 111.0, 113.0,
]
EXACT = 1e-12


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    close = np.array(CLOSES, dtype=float)
    return pd.DataFrame(
        {
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(close.size, 1000.0),
        },
        index=pd.date_range("2022-03-07", periods=close.size, freq="1D", tz="UTC"),
    )


# ------------------------------------------------------------------- SMA


def test_sma_golden(bars: pd.DataFrame) -> None:
    out = SMA(period=5).compute(bars)["sma_5"]

    # Bars 0..3 have fewer than 5 observations -> no value at all.
    assert out.iloc[:4].isna().all()

    # i=4: (100 + 101 + 102 + 103 + 104) / 5 = 510 / 5 = 102
    assert out.iloc[4] == pytest.approx(102.0, abs=EXACT)
    # i=9: (103 + 102 + 103 + 105 + 107) / 5 = 520 / 5 = 104
    assert out.iloc[9] == pytest.approx(104.0, abs=EXACT)
    # i=19: (109 + 108 + 109 + 111 + 113) / 5 = 550 / 5 = 110
    assert out.iloc[19] == pytest.approx(110.0, abs=EXACT)


# ------------------------------------------------------------------- EMA


def test_ema_golden(bars: pd.DataFrame) -> None:
    """EMA(3): alpha = 2/(3+1) = 0.5, seeded at bar 0 (adjust=False).

    e0 = 100
    e1 = 0.5*101 + 0.5*100     = 100.5
    e2 = 0.5*102 + 0.5*100.5   = 101.25
    e3 = 0.5*103 + 0.5*101.25  = 102.125
    e4 = 0.5*104 + 0.5*102.125 = 103.0625
    e5 = 0.5*103 + 0.5*103.0625= 103.03125
    e6 = 0.5*102 + 0.5*103.03125 = 102.515625
    Every term is an exact binary fraction, so these are exact equalities.
    """
    out = EMA(period=3).compute(bars)["ema_3"].to_numpy()
    assert out[0] == 100.0
    assert out[1] == 100.5
    assert out[2] == 101.25
    assert out[3] == 102.125
    assert out[4] == 103.0625
    assert out[5] == 103.03125
    assert out[6] == 102.515625


def test_wma_golden(bars: pd.DataFrame) -> None:
    """WMA(4): weights 1,2,3,4 (oldest..newest), denominator 1+2+3+4 = 10."""
    out = WMA(period=4).compute(bars)["wma_4"]
    assert out.iloc[:3].isna().all()
    # i=3: (1*100 + 2*101 + 3*102 + 4*103)/10 = (100+202+306+412)/10 = 1020/10
    assert out.iloc[3] == pytest.approx(102.0, abs=EXACT)
    # i=9: (1*102 + 2*103 + 3*105 + 4*107)/10 = (102+206+315+428)/10 = 1051/10
    assert out.iloc[9] == pytest.approx(105.1, abs=EXACT)


# ------------------------------------------------------------------- RSI


def test_rsi_golden(bars: pd.DataFrame) -> None:
    """RSI(3), Wilder: seed = simple mean of the first 3 gains/losses.

    deltas: i1..i9 = +1, +1, +1, +1, -1, -1, +1, +2, +2

    seed at i=3: avgG = (1+1+1)/3 = 1,   avgL = 0            -> RSI = 100
    i=4 (+1):    avgG = (1*2 + 1)/3 = 1, avgL = (0*2+0)/3 = 0 -> RSI = 100
    i=5 (-1):    avgG = (1*2 + 0)/3 = 2/3
                 avgL = (0*2 + 1)/3 = 1/3
                 RS = (2/3)/(1/3) = 2   -> RSI = 100 - 100/3 = 66.666...
    i=6 (-1):    avgG = (2/3*2 + 0)/3 = 4/9
                 avgL = (1/3*2 + 1)/3 = 5/9
                 RS = 4/5               -> RSI = 100 - 100/1.8 = 44.444...
    i=7 (+1):    avgG = (4/9*2 + 1)/3 = 17/27
                 avgL = (5/9*2 + 0)/3 = 10/27
                 RS = 17/10             -> RSI = 100 - 100/2.7 = 62.962...
    """
    out = RSI(period=3).compute(bars)["rsi_3"]
    assert out.iloc[:3].isna().all()  # 3 deltas needed, first is at i=1
    assert out.iloc[3] == pytest.approx(100.0, abs=EXACT)
    assert out.iloc[4] == pytest.approx(100.0, abs=EXACT)
    assert out.iloc[5] == pytest.approx(100.0 - 100.0 / 3.0, abs=EXACT)
    assert out.iloc[6] == pytest.approx(100.0 - 100.0 / 1.8, abs=EXACT)
    assert out.iloc[7] == pytest.approx(100.0 - 100.0 / 2.7, abs=EXACT)


# ------------------------------------------------------------------- ATR


def test_atr_golden(bars: pd.DataFrame) -> None:
    """ATR(3), Wilder-seeded.

    With high = close+1 and low = close-1, the true range of bar i is
        max(2, |close_i + 1 - close_{i-1}|, |close_i - 1 - close_{i-1}|)
    so a +/-1 close change gives TR = 2 and a +2 change gives TR = 3:

        TR = 2,2,2,2,2,2,2,2,3,3,2,2,2,3,3,2,2,2,3,3

    seed at i=2: (2+2+2)/3 = 2
    i=3..7: (2*2 + 2)/3 = 2                       (still exactly 2)
    i=8:    (2*2 + 3)/3 = 7/3   = 2.3333...
    i=9:    (7/3*2 + 3)/3 = 23/9 = 2.5555...
    i=10:   (23/9*2 + 2)/3 = 64/27 = 2.37037...
    """
    out = ATR(period=3).compute(bars)["atr_3"]
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(2.0, abs=EXACT)
    assert out.iloc[7] == pytest.approx(2.0, abs=EXACT)
    assert out.iloc[8] == pytest.approx(7.0 / 3.0, abs=EXACT)
    assert out.iloc[9] == pytest.approx(23.0 / 9.0, abs=EXACT)
    assert out.iloc[10] == pytest.approx(64.0 / 27.0, abs=EXACT)


# ------------------------------------------------------------------ MACD


def test_macd_golden(bars: pd.DataFrame) -> None:
    """MACD(3, 7, 3). alpha_fast = 0.5, alpha_slow = 0.25, alpha_signal = 0.5.

    EMA3 (from test_ema_golden):
        100, 100.5, 101.25, 102.125, 103.0625, 103.03125
    EMA7 (alpha = 2/(7+1) = 0.25, seeded at 100):
        f0 = 100
        f1 = 100      + 0.25*(101 - 100)      = 100.25
        f2 = 100.25   + 0.25*(102 - 100.25)   = 100.6875
        f3 = 100.6875 + 0.25*(103 - 100.6875) = 101.265625
        f4 = 101.265625 + 0.25*(104 - 101.265625) = 101.94921875
        f5 = 101.94921875 + 0.25*(103 - 101.94921875) = 102.2119140625
    line = EMA3 - EMA7:
        0, 0.25, 0.5625, 0.859375, 1.11328125, 0.8193359375
    signal = EMA3 of line, seeded at line[0] = 0:
        s0 = 0
        s1 = 0.5*0.25     + 0.5*0        = 0.125
        s2 = 0.5*0.5625   + 0.5*0.125    = 0.34375
        s3 = 0.5*0.859375 + 0.5*0.34375  = 0.6015625
        s4 = 0.5*1.11328125 + 0.5*0.6015625 = 0.857421875
        s5 = 0.5*0.8193359375 + 0.5*0.857421875 = 0.83837890625
    hist5 = 0.8193359375 - 0.83837890625 = -0.01904296875
    All exact binary fractions -> exact equality.
    """
    out = MACD(fast=3, slow=7, signal=3).compute(bars)
    line = out["macd_3_7_3_line"].to_numpy()
    sig = out["macd_3_7_3_signal"].to_numpy()
    hist = out["macd_3_7_3_hist"].to_numpy()

    assert line[0] == 0.0
    assert line[1] == 0.25
    assert line[2] == 0.5625
    assert line[3] == 0.859375
    assert line[4] == 1.11328125
    assert line[5] == 0.8193359375

    assert sig[1] == 0.125
    assert sig[2] == 0.34375
    assert sig[3] == 0.6015625
    assert sig[4] == 0.857421875
    assert sig[5] == 0.83837890625

    assert hist[5] == -0.01904296875


# ------------------------------------------------------------- BOLLINGER


def test_bollinger_golden(bars: pd.DataFrame) -> None:
    """Bollinger(5, 2 sd), POPULATION standard deviation (ddof = 0).

    i=4: window 100..104, mean = 510/5 = 102
         deviations -2,-1,0,1,2 -> squares 4+1+0+1+4 = 10, /5 = 2
         sd = sqrt(2) = 1.41421356...
         upper = 102 + 2*sqrt(2) = 104.828427...
         lower = 102 - 2*sqrt(2) =  99.171572...
         %b = (104 - lower) / (upper - lower)
            = (2 + 2*sqrt(2)) / (4*sqrt(2)) = (2 + sqrt(2))/4 = 0.853553...

    i=9: window 103,102,103,105,107, mean = 520/5 = 104
         deviations -1,-2,-1,1,3 -> squares 1+4+1+1+9 = 16, /5 = 3.2
         sd = sqrt(3.2) = 1.78885438...
    """
    ind = Bollinger(period=5, num_std=2.0)
    out = ind.compute(bars)
    mid, up, lo, pctb = (
        out[f"{ind.name}_mid"],
        out[f"{ind.name}_upper"],
        out[f"{ind.name}_lower"],
        out[f"{ind.name}_pctb"],
    )
    assert ind.name == "bb_5_2"

    assert mid.iloc[4] == pytest.approx(102.0, abs=EXACT)
    assert up.iloc[4] == pytest.approx(102.0 + 2.0 * math.sqrt(2.0), abs=EXACT)
    assert lo.iloc[4] == pytest.approx(102.0 - 2.0 * math.sqrt(2.0), abs=EXACT)
    assert pctb.iloc[4] == pytest.approx((2.0 + math.sqrt(2.0)) / 4.0, abs=EXACT)

    assert mid.iloc[9] == pytest.approx(104.0, abs=EXACT)
    assert up.iloc[9] == pytest.approx(104.0 + 2.0 * math.sqrt(3.2), abs=EXACT)
    assert lo.iloc[9] == pytest.approx(104.0 - 2.0 * math.sqrt(3.2), abs=EXACT)


def test_bollinger_uses_population_sigma_not_sample(bars: pd.DataFrame) -> None:
    """ddof=1 would give sqrt(10/4)=1.5811, not sqrt(10/5)=1.4142. Pin it."""
    ind = Bollinger(period=5, num_std=2.0)
    sd = (ind.compute(bars)[f"{ind.name}_upper"].iloc[4] - 102.0) / 2.0
    assert sd == pytest.approx(math.sqrt(2.0), abs=EXACT)
    assert sd != pytest.approx(math.sqrt(2.5), abs=1e-6)


# -------------------------------------------------------------- ICHIMOKU


def test_ichimoku_all_four_lines_golden(bars: pd.DataFrame) -> None:
    """Ichimoku(tenkan=3, kijun=5, senkou_b=7, senkou_shift=3).

    Because high = close+1 and low = close-1, a midline over p bars is
    ((max close + 1) + (min close - 1))/2 = (max close + min close)/2.

    TENKAN (3 bars)
        i=4: window closes 102,103,104 -> (104 + 102)/2 = 103
        i=9: window closes 103,105,107 -> (107 + 103)/2 = 105
    KIJUN (5 bars)
        i=4: window closes 100..104      -> (104 + 100)/2 = 102
        i=9: window closes 103,102,103,105,107 -> (107 + 102)/2 = 104.5
    SENKOU A = (tenkan + kijun)/2, displaced forward by 3 bars, so the value
    READABLE at bar i is the one computed at bar i-3:
        raw_a[4] = (103 + 102)/2 = 102.5  -> senkou_a[7]  = 102.5
        raw_a[9] = (105 + 104.5)/2 = 104.75 -> senkou_a[12] = 104.75
    SENKOU B = midline over 7 bars, displaced forward by 3:
        raw_b[6]  = closes 100..104,103,102 -> (104 + 100)/2 = 102
                                            -> senkou_b[9]  = 102
        raw_b[12] = closes 103,105,107,106,105,106 ... i.e. i=6..12
                    max 107, min 102        -> (107 + 102)/2 = 104.5
                                            -> senkou_b[15] = 104.5

    First readable bars: senkou A needs kijun (5) + 3 = bar 7; senkou B needs
    7 + 3 = bar 9. Nothing before that, and nothing from the future ever.
    """
    ind = Ichimoku(
        tenkan_period=3, kijun_period=5, senkou_b_period=7, senkou_shift=3,
        chikou_shift=3,
    )
    out = ind.compute(bars)
    p = ind.name
    tenkan, kijun = out[f"{p}_tenkan"], out[f"{p}_kijun"]
    sen_a, sen_b = out[f"{p}_senkou_a"], out[f"{p}_senkou_b"]

    assert tenkan.iloc[:2].isna().all()
    assert tenkan.iloc[4] == pytest.approx(103.0, abs=EXACT)
    assert tenkan.iloc[9] == pytest.approx(105.0, abs=EXACT)

    assert kijun.iloc[:4].isna().all()
    assert kijun.iloc[4] == pytest.approx(102.0, abs=EXACT)
    assert kijun.iloc[9] == pytest.approx(104.5, abs=EXACT)

    assert sen_a.iloc[:7].isna().all()
    assert sen_a.iloc[7] == pytest.approx(102.5, abs=EXACT)
    assert sen_a.iloc[12] == pytest.approx(104.75, abs=EXACT)

    assert sen_b.iloc[:9].isna().all()
    assert sen_b.iloc[9] == pytest.approx(102.0, abs=EXACT)
    assert sen_b.iloc[15] == pytest.approx(104.5, abs=EXACT)

    # Cloud geometry at i=9 uses the spans READABLE at i=9, i.e. raw values
    # from i=6:
    #   tenkan[6] = closes 104,103,102 -> (104 + 102)/2 = 103
    #   kijun[6]  = closes 102,103,104,103,102 -> (104 + 102)/2 = 103
    #   raw_a[6]  = (103 + 103)/2 = 103   -> senkou_a[9] = 103
    #   raw_b[6]  = 102 (above)           -> senkou_b[9] = 102
    # so cloud top = 103, cloud bottom = 102.
    assert sen_a.iloc[9] == pytest.approx(103.0, abs=EXACT)
    assert out[f"{p}_cloud_top"].iloc[9] == pytest.approx(103.0, abs=EXACT)
    assert out[f"{p}_cloud_bottom"].iloc[9] == pytest.approx(102.0, abs=EXACT)
    # close[9] = 107 > cloud top 103 -> price above cloud.
    assert out[f"{p}_price_vs_cloud"].iloc[9] == 1.0


def test_ichimoku_senkou_shift_is_independent_of_chikou_shift(
    bars: pd.DataFrame,
) -> None:
    """V1 displaced the cloud with chikou_shift, so sweeping chikou moved it."""
    base = Ichimoku(3, 5, 7, senkou_shift=3, chikou_shift=3)
    swept = Ichimoku(3, 5, 7, senkou_shift=3, chikou_shift=6)
    a = base.compute(bars)[f"{base.name}_senkou_a"].to_numpy()
    b = swept.compute(bars)[f"{swept.name}_senkou_a"].to_numpy()
    assert np.array_equal(np.isnan(a), np.isnan(b))
    assert np.array_equal(a[~np.isnan(a)], b[~np.isnan(b)])

    moved = Ichimoku(3, 5, 7, senkou_shift=5, chikou_shift=3)
    c = moved.compute(bars)[f"{moved.name}_senkou_a"]
    assert c.iloc[7] != pytest.approx(102.5, abs=EXACT)  # it DID move
    assert c.iloc[9] == pytest.approx(102.5, abs=EXACT)  # raw_a[4], now 5 later


# ------------------------------------------------------------- DONCHIAN


def test_donchian_golden(bars: pd.DataFrame) -> None:
    """Donchian(5). Highs are close+1, lows close-1.

    i=9: highs over i=5..9 -> max(104,103,104,106,108) = 108
         lows  over i=5..9 -> min(102,101,102,104,106) = 101
         mid = (108 + 101)/2 = 104.5
    The '_prior' channel at i=9 is the i=4..8 channel:
         highs 105,104,103,104,106 -> max 106
         lows  103,102,101,102,104 -> min 101
    """
    ind = Donchian(period=5)
    out = ind.compute(bars)
    assert out[f"{ind.name}_upper"].iloc[9] == pytest.approx(108.0, abs=EXACT)
    assert out[f"{ind.name}_lower"].iloc[9] == pytest.approx(101.0, abs=EXACT)
    assert out[f"{ind.name}_mid"].iloc[9] == pytest.approx(104.5, abs=EXACT)
    assert out[f"{ind.name}_upper_prior"].iloc[9] == pytest.approx(106.0, abs=EXACT)
    assert out[f"{ind.name}_lower_prior"].iloc[9] == pytest.approx(101.0, abs=EXACT)


# ---------------------------------------------------- SWING + FIBONACCI


def test_swing_confirmation_lag_golden(bars: pd.DataFrame) -> None:
    """SwingDetector(lookback=2) on the fixed series.

    highs: 101,102,103,104,105,104,103,104,106,108,107,106,107,109,111,110,...
    A swing HIGH at bar j needs high[j] strictly above both neighbouring 2-bar
    windows:
        j=4  (105): left max(103,104)=104, right max(104,103)=104  -> swing
        j=9  (108): left max(104,106)=106, right max(107,106)=107  -> swing
        j=14 (111): left max(107,109)=109, right max(110,109)=110  -> swing
    lows: 99,100,101,102,103,102,101,102,104,106,105,104,105,107,109,108,107,...
        j=6  (101): left min(103,102)=102, right min(102,104)=102  -> swing
        j=11 (104): left min(106,105)=105, right min(105,107)=105  -> swing
        j=16 (107): left min(109,108)=108, right min(108,110)=108  -> swing

    Each is published 2 bars later, on its confirmation bar. That lag is the
    whole point: bar 4's swing high is NOT knowable at bar 4.
    """
    out = SwingDetector(lookback=2).compute(bars)
    highs = out["swing_high_confirmed"]
    lows = out["swing_low_confirmed"]

    assert highs.notna().to_numpy().nonzero()[0].tolist() == [6, 11, 16]
    assert highs.iloc[6] == pytest.approx(105.0, abs=EXACT)
    assert highs.iloc[11] == pytest.approx(108.0, abs=EXACT)
    assert highs.iloc[16] == pytest.approx(111.0, abs=EXACT)
    assert np.isnan(highs.iloc[4])  # the swing bar itself publishes nothing

    assert lows.notna().to_numpy().nonzero()[0].tolist() == [8, 13, 18]
    assert lows.iloc[8] == pytest.approx(101.0, abs=EXACT)
    assert lows.iloc[13] == pytest.approx(104.0, abs=EXACT)
    assert lows.iloc[18] == pytest.approx(107.0, abs=EXACT)

    assert out["last_swing_high_bar"].iloc[11] == 9.0
    assert out["last_swing_high_bar"].iloc[16] == 14.0
    assert out["last_swing_low_bar"].iloc[13] == 11.0


def test_fibonacci_direction_aware_golden(bars: pd.DataFrame) -> None:
    """Fibonacci(swing_lookback=2), anchored by TIME ORDER of confirmed swings.

    At bar 12 the newest confirmed swings are the high 108 (bar 9) and the low
    101 (bar 6). The high is newer, so the leg runs low -> high (an UP leg):
        start = 101, end = 108, span = +7
        ret 0.500 = 108 - 0.5   * 7 = 104.5
        ret 0.618 = 108 - 0.618 * 7 = 103.674
        ext 1.618 = 101 + 1.618 * 7 = 112.326

    At bar 13 the low 104 (bar 11) is confirmed and is now the NEWER extreme,
    so the leg flips to high -> low (a DOWN leg):
        start = 108, end = 104, span = -4
        ret 0.500 = 104 - 0.5   * (-4) = 106
        ext 1.618 = 108 + 1.618 * (-4) = 101.528

    Note that at bar 13 the swing low (104) sits BELOW the swing high (108) but
    the anchors are ordered in time, not by magnitude. V1 tested `high > low`
    and emitted nothing whenever that failed -- entire instruments produced no
    Fibonacci signal at all and nothing said why.
    """
    ind = Fibonacci(swing_lookback=2, retracements=(0.5, 0.618), extensions=(1.618,))
    out = ind.compute(bars)
    p = ind.name

    assert out[f"{p}_dir"].iloc[12] == 1.0
    assert out[f"{p}_start"].iloc[12] == pytest.approx(101.0, abs=EXACT)
    assert out[f"{p}_end"].iloc[12] == pytest.approx(108.0, abs=EXACT)
    assert out[f"{p}_ret_0500"].iloc[12] == pytest.approx(104.5, abs=EXACT)
    assert out[f"{p}_ret_0618"].iloc[12] == pytest.approx(
        108.0 - 0.618 * 7.0, abs=EXACT
    )
    assert out[f"{p}_ext_1618"].iloc[12] == pytest.approx(
        101.0 + 1.618 * 7.0, abs=EXACT
    )

    assert out[f"{p}_dir"].iloc[13] == -1.0
    assert out[f"{p}_start"].iloc[13] == pytest.approx(108.0, abs=EXACT)
    assert out[f"{p}_end"].iloc[13] == pytest.approx(104.0, abs=EXACT)
    assert out[f"{p}_ret_0500"].iloc[13] == pytest.approx(106.0, abs=EXACT)
    assert out[f"{p}_ext_1618"].iloc[13] == pytest.approx(
        108.0 - 1.618 * 4.0, abs=EXACT
    )
