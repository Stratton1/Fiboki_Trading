"""Research robustness and integrity diagnostics (wave 3R): ADVISORY, beside the verdict.

Twelve diagnostics from the algorithmic research platforms (Freqtrade's
recursive and look-ahead analyses, StrategyQuant X / Build Alpha robustness
tests, pyfolio / QuantStats breakdowns, System Parameter Permutation):

* :mod:`.boundary`: the holdout fence every diagnostic stops at;
* :mod:`.ledger`: diagnostics computed from a stored run's own ledger
  (confidence bands, breakdowns, consistency, objectives, optimisation profile,
  intra-candle fill detail);
* :mod:`.engine`: re-running a stored binding through the ladder's own engine
  adapter with one input changed;
* :mod:`.rerun`: warm-up, look-ahead, execution stress, parameter perturbation,
  random-entry benchmark, price noise;
* :mod:`.store` and :mod:`.job`: the research job that runs them and the cache
  keyed by the engine fingerprint.

Nothing here is read by the validation ladder, a gate, a threshold or a filed
pre-registration (``research/preregistration``).
"""
