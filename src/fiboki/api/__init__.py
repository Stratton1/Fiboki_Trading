"""Fiboki V2 HTTP API.

This package is the ONLY way the operator workstation reaches the platform, and
it is built around four rules that V1's API broke:

1.  **Every figure is labelled.** A number leaves this API inside a
    :class:`fiboki.api.provenance.Figure`, which carries a
    :class:`fiboki.core.enums.Provenance`. There is no unlabelled float in any
    response model. V1 rendered backtest trades under a heading that said
    "Paper / Backtest" and the operator had no way to tell which was which.

2.  **Health is measured, not asserted.** ``/api/health`` reaches the database,
    reads the migration revision, reports the build SHA and the age of the
    worker heartbeat. V1 returned a hardcoded ``{"status": "ok"}`` and stayed
    green with the database down.

3.  **Caveats are computed, not written.** Any realism qualifier is derived
    server-side from the actual configuration in force and returned attached to
    the data it qualifies. V1 hardcoded "Estimated realistic return: 190-230%"
    into a page, next to a live computed value.

4.  **Live execution is not an API surface.** There is no endpoint, for any
    role, that can move the platform to LIVE. The controls live in
    :mod:`fiboki.broker.mode_guard` and start with a build-time constant.
    Asking for live via HTTP returns 403 with the list of controls.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Lazy re-export so importing the package does not import FastAPI."""
    from fiboki.api.app import create_app as _create_app

    return _create_app(*args, **kwargs)
