#!/usr/bin/env bash
# Fiboki V2: restore a scripts/backup.sh archive onto this checkout.
#
#   scripts/restore.sh ~/FibokiBackups/fiboki-backup-20260929T120000Z.tar.gz
#   scripts/restore.sh ARCHIVE --dry-run     # verify only, change nothing
#   scripts/restore.sh ARCHIVE --force       # restore over a var/ NEWER than the backup
#
# Order of operations, each a refusal point:
#   1. The archive's .sha256 (if present beside it) must match.
#   2. Every member must extract inside the staging directory (no absolute
#      paths, no "..", no links) and every file must match SHA256SUMS.
#   3. If the current var/ holds a file modified AFTER the backup was taken,
#      refuse without --force: restoring would silently discard newer records
#      (experiments, audit entries, paper trades).
#   4. If the API (:8000) is listening or uk.fiboki.worker is running, refuse:
#      restoring under a running writer corrupts both copies. Stop them with
#      scripts/launchd-install.sh --unload.
# Nothing is deleted. The current var/ is moved to var.pre-restore-<UTC>, and any
# ~/.fiboki file the archive replaces is moved to ~/.fiboki/pre-restore-<UTC>/.
# A market-data store not in the archive is carried over from the old var/.
# ~/.fiboki/env is never in an archive and is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
ARCHIVE=""
FORCE=0
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) ARCHIVE="$1"; shift ;;
  esac
done
[ -n "$ARCHIVE" ] || { echo "usage: $0 ARCHIVE [--dry-run] [--force]" >&2; exit 2; }
[ -f "$ARCHIVE" ] || { echo "no such archive: $ARCHIVE" >&2; exit 2; }

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

RUNNING=""
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  RUNNING="something is listening on :8000"
fi
if command -v launchctl >/dev/null 2>&1 \
   && launchctl print "gui/$(id -u)/uk.fiboki.worker" 2>/dev/null | grep -q "state = running"; then
  RUNNING="${RUNNING:+$RUNNING; }uk.fiboki.worker is running"
fi

"$PY" - "$ROOT" "$ARCHIVE" "$FORCE" "$DRY" "${FIBOKI_STATE_DIR:-$ROOT/var}" \
  "${FIBOKI_HOME:-$HOME/.fiboki}" "$RUNNING" <<'PY'
import hashlib, json, os, shutil, sys, tarfile, tempfile
from datetime import datetime, timezone
from pathlib import Path

root, archive, force, dry, state, home, running = sys.argv[1:8]
root, archive = Path(root), Path(archive).expanduser().resolve()
state, home = Path(state).expanduser().resolve(), Path(home).expanduser().resolve()
force, dry = force == "1", dry == "1"

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()

def fail(msg: str, code: int = 1) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(code)

side = archive.with_name(archive.name + ".sha256")
if side.exists():
    want = side.read_text().split()[0]
    if sha256(archive) != want:
        fail(f"{archive.name} does not match {side.name}; the archive is damaged or altered")
    print(f"archive sha256 matches {side.name}")
else:
    print(f"no {side.name} beside the archive; relying on the per-file SHA256SUMS")

stage_parent = Path(tempfile.mkdtemp(prefix="fiboki-restore-", dir=str(state.parent)))
try:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members}
        if len(tops) != 1:
            fail(f"expected one top-level directory, found {sorted(tops)}")
        for m in members:
            if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                fail(f"unsafe member {m.name!r}")
        tar.extractall(stage_parent, filter="data")
    stage = stage_parent / tops.pop()
    manifest = json.loads((stage / "MANIFEST.json").read_text())
    listed: dict[str, str] = {}
    for line in (stage / "SHA256SUMS").read_text().splitlines():
        if line.strip():
            digest, rel = line.split("  ", 1)
            listed[rel] = digest
    present = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    present -= {"MANIFEST.json", "SHA256SUMS"}
    if present != set(listed):
        fail(f"file set differs from SHA256SUMS: extra {sorted(present - set(listed))[:5]}, "
             f"missing {sorted(set(listed) - present)[:5]}")
    bad = [rel for rel, digest in listed.items() if sha256(stage / rel) != digest]
    if bad:
        fail(f"{len(bad)} file(s) do not match SHA256SUMS, e.g. {bad[:5]}")
    print(f"verified  {len(listed)} files against SHA256SUMS")
    created = datetime.fromisoformat(manifest["created_at"])
    print(f"backup    {manifest['created_at']} from {manifest.get('host')} "
          f"(commit {str(manifest.get('git_head'))[:12]}), datastore "
          f"{'included' if manifest.get('includes_datastore') else 'not included'}")

    # logs/ is never restored, and a datastore the archive does not carry is
    # carried over rather than replaced, so neither can be "discarded".
    skip = {"logs"} | (set() if manifest.get("includes_datastore") else {"datastore"})
    newest = None
    if state.is_dir():
        for p in state.rglob("*"):
            if p.is_file() and not skip.intersection(p.relative_to(state).parts[:1]):
                t = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if newest is None or t > newest[0]:
                    newest = (t, p)
    if newest is not None and newest[0] > created:
        msg = (f"{newest[1]} was modified at {newest[0].isoformat()}, after the backup "
               f"({created.isoformat()}); restoring would discard newer records")
        if not force:
            fail(msg + ". Re-run with --force if that is what you want.")
        print(f"WARNING (--force): {msg}")
    if running:
        fail(f"{running}. Stop the services first: scripts/launchd-install.sh --unload")
    if dry:
        print("dry run: verified, nothing changed")
        sys.exit(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    new_var = stage / "var"
    for label, target in (("experiments.sqlite", "experiments.sqlite"), ("paper", "paper"),
                          ("datastore", "datastore")):
        ext = stage / "external" / label
        if ext.exists():
            dest = new_var / target
            if dest.exists():
                fail(f"archive has both var/{target} and external/{label}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(ext), str(dest))
            print(f"external/{label} -> {state / target} (check FIBOKI_* paths in ~/.fiboki/env)")
    aside = None
    if state.exists():
        aside = state.with_name(f"{state.name}.pre-restore-{stamp}")
        state.rename(aside)
        print(f"moved     {state} -> {aside}")
    shutil.move(str(new_var), str(state))
    if aside is not None:
        for keep in ("datastore", "logs"):
            if (aside / keep).exists() and not (state / keep).exists():
                (aside / keep).rename(state / keep)
                print(f"carried   {keep}/ over from the previous state directory")
    (state / "logs").mkdir(exist_ok=True)
    src_home = stage / "fiboki_home"
    if src_home.is_dir():
        backup_dir = home / f"pre-restore-{stamp}"
        for p in sorted(x for x in src_home.rglob("*") if x.is_file()):
            rel = p.relative_to(src_home)
            if rel.as_posix() == "env":
                continue
            dest = home / rel
            if dest.exists():
                (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                dest.rename(backup_dir / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        print(f"restored  {home} (replaced files kept in {backup_dir} if any)")
    print("done. Next: .venv/bin/fiboki doctor, then scripts/launchd-install.sh --load")
finally:
    shutil.rmtree(stage_parent, ignore_errors=True)
PY
