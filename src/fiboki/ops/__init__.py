"""Operator controls and evidence for the PAPER fleet (Wave 3O).

Everything here is either a durable record an operator's act writes (per-bot
pause / resume / drain, the structured trade journal), an append-only proof of
what the paper runtime did (the hash-chained paper audit log), or an ADVISORY
computation over stored records (backtest-versus-paper reconciliation, the
paper maturity report, self-correlation, risk of ruin, portfolio weights).

Nothing in this package places, sizes or approves an order, changes a risk
limit, disarms the kill switch or enables an execution mode. The one control
that changes paper behaviour, a bot's control state, can only REMOVE new
entries: it never adds risk and never touches an open position's exits.

Layering: above ``lifecycle`` (it reads stored backtests, the sizing rule and
the bootstrap), below ``workers``, ``api`` and ``entrypoints``, which read it.
"""
