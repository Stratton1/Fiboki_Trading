"""Rolling volatility indicator."""

import numpy as np
import pandas as pd

from fibokei.indicators.base import Indicator


class RollingVolatility(Indicator):
    """Standard deviation of log returns over a rolling window."""

    def __init__(self, period: int = 20):
        self.period = period

    @property
    def name(self) -> str:
        return "rolling_volatility"

    @property
    def warmup_period(self) -> int:
        return self.period + 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        self.validate_input(df)
        ratio = df["close"] / df["close"].shift(1)
        ratio = ratio.clip(lower=1e-10)  # Guard against zero/negative prices
        log_returns = np.log(ratio)
        df["rolling_vol"] = log_returns.rolling(window=self.period).std()
        return df
