"""Channel / band indicators (centralised). Closed-candle, deterministic."""

import pandas as pd

from fibokei.indicators.base import Indicator


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


class BollingerBands(Indicator):
    """Bollinger Bands. Adds ``bb_mid``, ``bb_upper``, ``bb_lower``, ``bb_pctb``."""

    def __init__(self, period: int = 20, num_std: float = 2.0):
        if period < 2:
            raise ValueError("Bollinger period must be >= 2")
        self.period, self.num_std = period, num_std

    @property
    def name(self) -> str:
        return f"bb_{self.period}_{self.num_std}"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        mid = df["close"].rolling(self.period).mean()
        std = df["close"].rolling(self.period).std(ddof=0)
        upper = mid + self.num_std * std
        lower = mid - self.num_std * std
        df["bb_mid"] = mid
        df["bb_upper"] = upper
        df["bb_lower"] = lower
        width = (upper - lower).replace(0.0, float("nan"))
        df["bb_pctb"] = (df["close"] - lower) / width
        return df


class DonchianChannels(Indicator):
    """Donchian Channels. Adds ``donchian_upper``, ``donchian_lower``, ``donchian_mid``."""

    def __init__(self, period: int = 20):
        if period < 2:
            raise ValueError("Donchian period must be >= 2")
        self.period = period

    @property
    def name(self) -> str:
        return f"donchian_{self.period}"

    @property
    def warmup_period(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        upper = df["high"].rolling(self.period).max()
        lower = df["low"].rolling(self.period).min()
        df["donchian_upper"] = upper
        df["donchian_lower"] = lower
        df["donchian_mid"] = (upper + lower) / 2.0
        return df


class KeltnerChannels(Indicator):
    """Keltner Channels (EMA mid + ATR bands). Adds ``kc_mid``, ``kc_upper``, ``kc_lower``."""

    def __init__(self, ema_period: int = 20, atr_period: int = 10, multiple: float = 2.0):
        if min(ema_period, atr_period) < 1:
            raise ValueError("Keltner periods must be >= 1")
        self.ema_period, self.atr_period, self.multiple = ema_period, atr_period, multiple

    @property
    def name(self) -> str:
        return f"kc_{self.ema_period}_{self.atr_period}_{self.multiple}"

    @property
    def warmup_period(self) -> int:
        return max(self.ema_period, self.atr_period) + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        mid = df["close"].ewm(span=self.ema_period, adjust=False).mean()
        atr = _true_range(df).ewm(alpha=1.0 / self.atr_period, adjust=False).mean()
        df["kc_mid"] = mid
        df["kc_upper"] = mid + self.multiple * atr
        df["kc_lower"] = mid - self.multiple * atr
        return df
