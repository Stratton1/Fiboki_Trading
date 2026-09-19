# syntax=docker/dockerfile:1.7
#
# Multi-stage, non-root, digest-pinned.
#
# Why the digest pin
# ------------------
# `python:3.11-slim` is a moving tag: the image you built the golden results
# against is not the image you get next month.  A pinned digest makes the
# runtime a recorded fact in the same way `scripts/lockfile.py` makes the
# Python environment one.  Update it deliberately, and re-run the golden tests
# when you do.
#
#   Refresh with:
#     docker buildx imagetools inspect python:3.11-slim-bookworm | head
#
# Note this image is the CLOUD path.  The primary way Fiboki runs is a worker
# process on the operator's Mac under launchd (see deploy/launchd/), because
# the machine doing the research is the machine the operator is sitting at.

# ---------------------------------------------------------------- base
FROM python@sha256:6c1f1f0d1f1ac9d2bd0e5cb0e3f9fcf1b5f9c1ad6b1ba0b9e6f2dfb5a9d0c4e7 AS base
# ^ python:3.11-slim-bookworm. Replace with the digest your registry reports;
#   the build fails loudly on a mismatch rather than silently drifting.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---------------------------------------------------------------- builder
FROM base AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md* ./
COPY deploy/requirements.lock deploy/constraints.txt ./deploy/
COPY src ./src

# Install into a self-contained venv so the runtime stage copies one directory
# and carries no build toolchain.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip==24.3.1 \
 && pip install -c deploy/constraints.txt . \
 && python -c "import fiboki, fiboki.workers, fiboki.obs, fiboki.cli; print(fiboki.__version__)"

# ---------------------------------------------------------------- runtime
FROM base AS runtime

# Non-root, fixed uid so a bind-mounted data directory has predictable
# ownership on Linux hosts.
RUN groupadd --gid 10001 fiboki \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin fiboki

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=fiboki:fiboki scripts ./scripts
COPY --chown=fiboki:fiboki deploy/requirements.lock ./deploy/requirements.lock

# Data and state live on volumes. Nothing durable is written into the image.
ENV FIBOKI_HOME=/var/lib/fiboki \
    FIBOKI_STATE_DB=/var/lib/fiboki/state.db \
    FIBOKI_DATA_ROOT=/var/lib/fiboki/data \
    FIBOKI_ALERT_LOG=/var/log/fiboki/alerts.jsonl \
    FIBOKI_LOG_FORMAT=json \
    FIBOKI_EXECUTION_MODE=paper
# NOTE: FIBOKI_LIVE_EXECUTION_ENABLED is deliberately NOT set here, not even to
# "false". scripts/check_live_flags.py fails the build on a truthy literal in
# committed config; the safest committed value is no value at all.

RUN mkdir -p /var/lib/fiboki /var/log/fiboki \
 && chown -R fiboki:fiboki /var/lib/fiboki /var/log/fiboki

USER fiboki

# A healthcheck that actually checks. `fiboki system health` exits non-zero
# when the database is unreachable, the schema is behind, or a worker
# heartbeat is stale -- unlike V1's /health, which returned ok unconditionally
# from a handler that performed no work.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["fiboki", "system", "health"]

ENTRYPOINT ["fiboki"]
CMD ["system", "doctor"]
