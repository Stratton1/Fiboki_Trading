#!/usr/bin/env bash
# Fiboki V2: write the API's OpenAPI schema for frontend type generation.
#
#   scripts/gen-openapi.sh                 -> apps/web/openapi.json
#   scripts/gen-openapi.sh path/out.json   -> that path
#
# Builds the FastAPI app in-process (no server, no network) against a throwaway
# state directory and dumps app.openapi(), which is exactly what a running API
# serves at GET /api/openapi.json, including components.securitySchemes and the
# per-operation x-fiboki-auth level. The ambient FIBOKI_* environment is not
# read: settings come from an explicit dict, so the schema does not depend on
# the machine that generated it. Then run openapi-typescript in apps/web.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/apps/web/openapi.json}"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="python3"; fi
fi
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT

cd "$ROOT"
FIBOKI_GEN_STATE="$STATE" FIBOKI_GEN_OUT="$OUT" "$PY" - <<'PYEOF'
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from fiboki.api.app import create_app  # noqa: E402
from fiboki.api.settings import load_settings  # noqa: E402

settings = load_settings({"FIBOKI_STATE_DIR": os.environ["FIBOKI_GEN_STATE"]})
schema = create_app(settings, configure_logs=False).openapi()
out = Path(os.environ["FIBOKI_GEN_OUT"])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")
PYEOF
