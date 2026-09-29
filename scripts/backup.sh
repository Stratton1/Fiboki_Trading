#!/usr/bin/env bash
# Fiboki V2: back up the operator state to one verifiable archive.
#
#   scripts/backup.sh                          # -> ~/FibokiBackups/fiboki-backup-<UTC>.tar.gz
#   scripts/backup.sh --dest /Volumes/External
#   scripts/backup.sh --include-datastore      # also the market-data store (large)
#
# What goes in (paths resolved exactly as scripts/fiboki-service.sh resolves them):
#   var/                     the whole state directory: agent audit ledger, research
#                            store, news store, event stores, paper journal, kill
#                            switch, sessions, alerts. EXCLUDED: var/datastore (unless
#                            --include-datastore) and var/logs.
#   external/                FIBOKI_EXPERIMENT_DB, FIBOKI_PAPER_ROOT and (with the flag)
#                            FIBOKI_DATA_ROOT when they live OUTSIDE the state directory.
#   fiboki_home/             ~/.fiboki (state.db: leases and heartbeats; digest cache).
#                            EXCLUDED: ~/.fiboki/env, because it holds secrets. Back that
#                            file up in your password manager, not in an archive.
#
# How: every SQLite database is copied with SQLite's online backup API (a
# consistent snapshot even while a service writes); other files are copied as
# they are. An append-only JSONL file copied while a service appends can end in
# a partial line, so stop the services first for a clean backup:
#   scripts/launchd-install.sh --unload
# Every file is listed with its SHA-256 in SHA256SUMS; MANIFEST.json records
# when, from where and from which commit; the archive gets a .sha256 beside it.
# Nothing is deleted or modified at the source.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEST="$HOME/FibokiBackups"
INCLUDE_DATASTORE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:?}"; shift 2 ;;
    --include-datastore) INCLUDE_DATASTORE=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

FIBOKI_HOME_DIR="${FIBOKI_HOME:-$HOME/.fiboki}"
STATE_DIR="${FIBOKI_STATE_DIR:-$ROOT/var}"
if [ -f "$FIBOKI_HOME_DIR/env" ]; then
  # Only the path settings are read from the env file; nothing is exported.
  # shellcheck disable=SC1090
  eval "$(grep -E '^(export )?FIBOKI_(STATE_DIR|DATA_ROOT|EXPERIMENT_DB|PAPER_ROOT)=' "$FIBOKI_HOME_DIR/env" | sed 's/^export //; s/^/local_/')" 2>/dev/null || true
fi
STATE_DIR="${FIBOKI_STATE_DIR:-${local_FIBOKI_STATE_DIR:-$STATE_DIR}}"
DATA_ROOT="${FIBOKI_DATA_ROOT:-${local_FIBOKI_DATA_ROOT:-$STATE_DIR/datastore}}"
EXPERIMENT_DB="${FIBOKI_EXPERIMENT_DB:-${local_FIBOKI_EXPERIMENT_DB:-$STATE_DIR/experiments.sqlite}}"
PAPER_ROOT="${FIBOKI_PAPER_ROOT:-${local_FIBOKI_PAPER_ROOT:-$STATE_DIR/paper}}"

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: something is listening on :8000 (the API?). SQLite files are snapshotted"
  echo "         consistently, but a JSONL ledger being appended to may end in a partial line."
  echo "         For a clean backup: scripts/launchd-install.sh --unload, then re-run."
fi

mkdir -p "$DEST"
"$PY" - "$ROOT" "$STATE_DIR" "$DATA_ROOT" "$EXPERIMENT_DB" "$PAPER_ROOT" "$FIBOKI_HOME_DIR" \
  "$DEST" "$INCLUDE_DATASTORE" <<'PY'
import hashlib, json, os, shutil, socket, sqlite3, subprocess, sys, tarfile, tempfile
from datetime import datetime, timezone
from pathlib import Path

root, state, data_root, exp_db, paper, home, dest, inc_ds = sys.argv[1:9]
root, state, data_root, exp_db, paper, home, dest = map(
    lambda p: Path(p).expanduser().resolve(), (root, state, data_root, exp_db, paper, home, dest))
include_datastore = inc_ds == "1"
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
name = f"fiboki-backup-{stamp}"
SQLITE_MAGIC = b"SQLite format 3\x00"
SIDE_FILES = ("-wal", "-shm", "-journal")

def is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False

def inside(p: Path, parent: Path) -> bool:
    try:
        p.relative_to(parent)
        return True
    except ValueError:
        return False

def copy_one(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_sqlite(src):
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30) as s, \
             sqlite3.connect(dst) as d:
            s.backup(d)
        return "sqlite-backup"
    shutil.copy2(src, dst)
    return "copy"

def walk(src: Path, dst: Path, exclude: list[Path]) -> dict[str, int]:
    counts = {"sqlite-backup": 0, "copy": 0}
    if src.is_file():
        counts[copy_one(src, dst)] += 1
        return counts
    for dirpath, dirnames, filenames in os.walk(src):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not any((here / d) == e for e in exclude)]
        sqlite_here = {f for f in filenames if is_sqlite(here / f)}
        for fname in filenames:
            if any(fname.endswith(s) and fname[: -len(s)] in sqlite_here for s in SIDE_FILES):
                continue  # captured by the online backup of the database itself
            if fname.endswith(".tmp"):
                continue
            p = here / fname
            if p.is_symlink() or not p.is_file():
                continue
            counts[copy_one(p, dst / p.relative_to(src))] += 1
    return counts

with tempfile.TemporaryDirectory(prefix="fiboki-backup-", dir=dest) as tmp:
    stage = Path(tmp) / name
    stage.mkdir()
    sources: dict[str, dict[str, object]] = {}
    exclude = [state / "logs"]
    if not include_datastore and inside(data_root, state):
        exclude.append(data_root)
    if not state.is_dir():
        sys.exit(f"state directory {state} does not exist; nothing to back up")
    sources["var"] = {"from": str(state), **walk(state, stage / "var", exclude)}
    for label, path in (("experiments.sqlite", exp_db), ("paper", paper)):
        if path.exists() and not inside(path, state):
            sources[f"external/{label}"] = {"from": str(path),
                                            **walk(path, stage / "external" / label, [])}
    if include_datastore:
        if not (data_root / ".fiboki-data-root").exists():
            sys.exit(f"--include-datastore: {data_root} is not a marked data root; refusing")
        if not inside(data_root, state):
            sources["external/datastore"] = {"from": str(data_root),
                                             **walk(data_root, stage / "external" / "datastore", [])}
    if home.is_dir():
        sources["fiboki_home"] = {"from": str(home),
                                  **walk(home, stage / "fiboki_home", [home / "env", home / "logs"])}
        (stage / "fiboki_home" / "env").unlink(missing_ok=True)

    sums: list[str] = []
    total = 0
    for p in sorted(x for x in stage.rglob("*") if x.is_file()):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(8 << 20), b""):
                h.update(block)
        rel = p.relative_to(stage).as_posix()
        sums.append(f"{h.hexdigest()}  {rel}")
        total += p.stat().st_size
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=False).stdout.strip() or None
    except OSError:
        head = None
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "repo": str(root),
        "git_head": head,
        "includes_datastore": include_datastore,
        "excludes": ["var/logs", "~/.fiboki/env (secrets)"]
                    + ([] if include_datastore else ["datastore"]),
        "sources": sources,
        "files": len(sums),
        "bytes": total,
    }
    (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    archive = dest / f"{name}.tar.gz"
    with tarfile.open(archive.with_suffix(".gz.partial"), "w:gz") as tar:
        tar.add(stage, arcname=name)
    os.replace(archive.with_suffix(".gz.partial"), archive)

h = hashlib.sha256()
with open(archive, "rb") as f:
    for block in iter(lambda: f.read(8 << 20), b""):
        h.update(block)
(archive.parent / (archive.name + ".sha256")).write_text(f"{h.hexdigest()}  {archive.name}\n")
print(f"archive   {archive}")
print(f"sha256    {h.hexdigest()}")
print(f"files     {manifest['files']} ({manifest['bytes']:,} bytes before compression)")
print(f"datastore {'included' if include_datastore else 'EXCLUDED (use --include-datastore)'}")
print("excluded  ~/.fiboki/env (secrets) and var/logs")
PY
