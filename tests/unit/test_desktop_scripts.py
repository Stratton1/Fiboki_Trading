"""The desktop deployment scripts, run for real against temporary directories.

Nothing here touches the operator's machine: every script is pointed at a
``tmp_path`` (``--target``, ``--dest``, ``FIBOKI_STATE_DIR``, ``FIBOKI_HOME``)
and ``llama-server`` is a stub on ``PATH``. What cannot be exercised on Linux
(launchctl, Homebrew, a real model) is stated in the report, not faked here.
"""
from __future__ import annotations

import http.server
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import tarfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
NEW_SCRIPTS = ("desktop-install.sh", "backup.sh", "restore.sh", "llama-server.sh",
               "launchd-install.sh", "fiboki-service.sh")
SERVICES = ("api", "worker", "web", "news", "llama")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(argv: list[str], env: dict[str, str] | None = None, cwd: Path = REPO,
         timeout: float = 120) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("FIBOKI_", "FIBOKEI_"))}
    return subprocess.run(argv, env={**base, **(env or {})}, cwd=cwd, capture_output=True,
                          text=True, timeout=timeout, check=False)


@pytest.mark.parametrize(
    "name", [*NEW_SCRIPTS, "desktop/Start Fiboki.command", "desktop/fiboki-launch.sh",
             "desktop/install-launcher.sh"]
)
def test_every_script_parses_and_sets_no_live_control(name: str) -> None:
    path = SCRIPTS / name
    assert _run(["bash", "-n", str(path)]).returncode == 0
    text = path.read_text()
    for flag in ("FIBOKI_LIVE_EXECUTION_ENABLED", "FIBOKI_LIVE_RUNTIME_ARMED",
                 "FIBOKI_OANDA_LIVE_RUNTIME"):
        assert not re.search(rf"(export\s+)?{flag}=", text), f"{name} assigns {flag}"
    assert "FIBOKI_LLM_URL" not in text or name == "desktop/Start Fiboki.command"


def test_the_launcher_no_longer_exports_an_undeclared_variable() -> None:
    text = (SCRIPTS / "desktop" / "Start Fiboki.command").read_text()
    assert "export FIBOKI_LLM_URL" not in text
    assert "FIBOKI_AGENT_LOCAL_URL" in text


# ------------------------------------------------------------ llama-server


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


def _stub_llama(bin_dir: Path, version_line: str) -> None:
    stub = bin_dir / "llama-server"
    stub.write_text(f"#!/bin/sh\necho '{version_line}'\necho 'built with clang for arm64'\n")
    stub.chmod(0o755)


def _llama(args: list[str], bin_dir: Path) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(SCRIPTS / "llama-server.sh"), *args],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(bin_dir.parent)})


def test_llama_server_prints_the_pinned_command_for_a_tier(tmp_path: Path, stub_bin: Path) -> None:
    _stub_llama(stub_bin, "version: 6700 (build 6700, commit abc1234)")
    models = tmp_path / "Models"
    model = models / "Qwen3-32B-GGUF" / "Qwen3-32B-Q5_K_M.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"GGUF")
    proc = _llama(["--print", "--ram-gb", "64", "--models-dir", str(models)], stub_bin)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "b6700" in out and "qwen3-32b-q5_k_m" in out
    command = out.split("command    :", 1)[1]
    for piece in (f"-m {model}", "--ctx-size 16384", "-np 1", "-fa on", "--host 127.0.0.1",
                  "--port 8080", "--reasoning-budget 0", "--alias qwen3-32b-q5_k_m"):
        assert piece in command, piece


@pytest.mark.parametrize(("ram", "repo", "file"), [
    ("32", "Qwen/Qwen3-14B-GGUF", "Qwen3-14B-Q5_K_M.gguf"),
    ("64", "Qwen/Qwen3-32B-GGUF", "Qwen3-32B-Q5_K_M.gguf"),
    ("192", "Qwen/Qwen3-32B-GGUF", "Qwen3-32B-Q8_0.gguf"),
])
def test_a_missing_model_prints_the_download_and_never_fetches(
    tmp_path: Path, stub_bin: Path, ram: str, repo: str, file: str
) -> None:
    _stub_llama(stub_bin, "version: 6700 (build 6700, commit abc1234)")
    models = tmp_path / "Models"
    proc = _llama(["--ram-gb", ram, "--models-dir", str(models)], stub_bin)
    assert proc.returncode == 1
    assert f"hf download {repo} {file}" in proc.stderr
    assert f"https://huggingface.co/{repo}/resolve/main/{file}" in proc.stderr
    assert not models.exists(), "the script must not create or download anything"


def test_an_old_build_is_refused_and_the_old_version_format_is_read(
    tmp_path: Path, stub_bin: Path
) -> None:
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    _stub_llama(stub_bin, "version: 5000 (build 5000, commit 1a2b3c4)")
    proc = _llama(["--print", "--model", str(model)], stub_bin)
    assert proc.returncode == 1 and "older than b6325" in proc.stderr
    _stub_llama(stub_bin, "version: 6400 (c466abe1)")  # pre-2026 format
    proc = _llama(["--print", "--model", str(model)], stub_bin)
    assert proc.returncode == 0 and "b6400" in proc.stdout


def test_a_relative_model_path_is_refused(stub_bin: Path) -> None:
    _stub_llama(stub_bin, "version: 6700 (build 6700, commit abc1234)")
    proc = _llama(["--print", "--model", "models/x.gguf"], stub_bin)
    assert proc.returncode == 2 and "absolute" in proc.stderr


# ----------------------------------------------------------------- launchd


def test_launchd_install_substitutes_the_repo_into_five_valid_plists(tmp_path: Path) -> None:
    target = tmp_path / "LaunchAgents"
    proc = _run(["bash", str(SCRIPTS / "launchd-install.sh"), "--target", str(target)])
    assert proc.returncode == 0, proc.stderr
    assert "Not loaded" in proc.stdout
    for name in SERVICES:
        path = target / f"uk.fiboki.{name}.plist"
        text = path.read_text()
        assert "__FIBOKI_ROOT__" not in text
        plist = plistlib.loads(text.encode())
        assert plist["Label"] == f"uk.fiboki.{name}"
        assert plist["ProgramArguments"] == [
            "/bin/bash", f"{REPO}/scripts/fiboki-service.sh", name]
        assert plist["WorkingDirectory"] == str(REPO)
        assert plist["KeepAlive"] == {"SuccessfulExit": False}
        assert plist["ThrottleInterval"] >= 30
        assert plist["StandardOutPath"] == f"{REPO}/var/logs/{name}.log"
        assert plist["EnvironmentVariables"] == {"FIBOKI_EXECUTION_MODE": "paper"}
    worker = plistlib.loads((target / "uk.fiboki.worker.plist").read_bytes())
    assert worker["ExitTimeOut"] > 30, "longer than WorkerConfig.shutdown_grace_seconds"
    again = _run(["bash", str(SCRIPTS / "launchd-install.sh"), "--target", str(target)])
    assert again.returncode == 0, "idempotent"


def test_the_service_wrapper_forces_paper_after_reading_the_env_file() -> None:
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    read = text.index('done < "$ENV_FILE"')
    forced = text.index("export FIBOKI_EXECUTION_MODE=paper")
    unset = text.index("unset FIBOKI_LIVE_EXECUTION_ENABLED FIBOKI_LIVE_RUNTIME_ARMED")
    assert read < forced and read < unset
    # Never sourced: scrypt hashes contain `$` and would be expanded away.
    assert '. "$ENV_FILE"' not in text and 'source "$ENV_FILE"' not in text
    assert _run(["bash", str(SCRIPTS / "fiboki-service.sh"), "bogus"]).returncode == 2


def test_the_service_wrapper_reads_env_values_without_expansion(tmp_path: Path) -> None:
    """The reader block of fiboki-service.sh, run on its own against a file with
    `$`-laden and quoted values: every value comes through verbatim, malformed
    keys are skipped, and a value trying to force live mode is still overridden
    by the paper-only block that follows it."""
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    block = text[text.index('ENV_FILE="${FIBOKI_HOME'):text.index("# ---- desktop defaults")]
    env = tmp_path / "env"
    env.write_text(
        "# comment\n"
        "\n"
        "FIBOKI_OPERATORS='joe:scrypt$16384$8$1$abc$def'\n"
        'FIBOKI_SESSION_SECRET="s3cr$et=with=equals"\n'
        "PLAIN=$HOME/unexpanded\n"
        "bad-key=ignored\n"
        "FIBOKI_EXECUTION_MODE=live\n"
    )
    script = (
        "set -euo pipefail\n"
        f"{block}\n"
        'printf "%s\\n" "$FIBOKI_OPERATORS" "$FIBOKI_SESSION_SECRET" "$PLAIN" '
        '"${FIBOKI_EXECUTION_MODE}" "${bad_key:-unset}"\n'
    )
    run = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"HOME": str(tmp_path), "FIBOKI_HOME": str(tmp_path), "PATH": os.environ["PATH"]},
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "joe:scrypt$16384$8$1$abc$def", "s3cr$et=with=equals", "$HOME/unexpanded", "paper", "unset",
    ]
    assert "ignoring malformed line" in run.stderr and "bad-key" in run.stderr


# ------------------------------------------------------- backup and restore


@pytest.fixture
def state(tmp_path: Path) -> dict[str, Path]:
    var = tmp_path / "var"
    (var / "agents").mkdir(parents=True)
    (var / "agents" / "audit.jsonl").write_text('{"seq": 1}\n')
    db = var / "experiments.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE experiment (id TEXT)")
        conn.execute("INSERT INTO experiment VALUES ('exp_1')")
    (var / "datastore").mkdir()
    (var / "datastore" / ".fiboki-data-root").write_text("")
    (var / "datastore" / "bars.parquet").write_bytes(b"x" * 100)
    (var / "logs").mkdir()
    (var / "logs" / "api.log").write_text("noise\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / "env").write_text("FIBOKI_SESSION_SECRET=do-not-archive\n")
    (home / "state.db").write_bytes(b"not really sqlite")
    return {"var": var, "home": home, "dest": tmp_path / "backups"}


def _env(state: dict[str, Path]) -> dict[str, str]:
    return {"FIBOKI_STATE_DIR": str(state["var"]), "FIBOKI_HOME": str(state["home"]),
            "HOME": str(state["home"].parent)}


def _backup(state: dict[str, Path], *extra: str) -> Path:
    proc = _run(["bash", str(SCRIPTS / "backup.sh"), "--dest", str(state["dest"]), *extra],
                env=_env(state))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    (archive,) = sorted(state["dest"].glob("fiboki-backup-*.tar.gz"))
    return archive


def test_backup_writes_a_manifest_checksums_and_excludes_secrets_and_the_datastore(
    state: dict[str, Path]
) -> None:
    archive = _backup(state)
    side = archive.with_name(archive.name + ".sha256")
    assert side.exists()
    with tarfile.open(archive) as tar:
        names = {m.name.split("/", 1)[1] for m in tar.getmembers() if "/" in m.name}
        top = tar.getmembers()[0].name.split("/", 1)[0]
        manifest = json.load(tar.extractfile(f"{top}/MANIFEST.json"))  # type: ignore[arg-type]
        sums = tar.extractfile(f"{top}/SHA256SUMS").read().decode()  # type: ignore[union-attr]
    assert "var/agents/audit.jsonl" in names and "var/experiments.sqlite" in names
    assert "fiboki_home/state.db" in names
    assert not any(n.startswith("var/datastore") for n in names)
    assert not any(n.startswith("var/logs") for n in names)
    assert "fiboki_home/env" not in names
    assert manifest["includes_datastore"] is False and manifest["files"] == 3
    assert "var/experiments.sqlite" in sums
    with_ds = _backup_again(state, "--include-datastore")
    with tarfile.open(with_ds) as tar:
        assert any("var/datastore/bars.parquet" in m.name for m in tar.getmembers())


def _backup_again(state: dict[str, Path], *extra: str) -> Path:
    before = set(state["dest"].glob("fiboki-backup-*.tar.gz"))
    time.sleep(1.1)  # the archive name has one-second resolution
    proc = _run(["bash", str(SCRIPTS / "backup.sh"), "--dest", str(state["dest"]), *extra],
                env=_env(state))
    assert proc.returncode == 0, proc.stderr
    (new,) = set(state["dest"].glob("fiboki-backup-*.tar.gz")) - before
    return new


def test_the_sqlite_snapshot_is_a_readable_database(state: dict[str, Path], tmp_path: Path) -> None:
    archive = _backup(state)
    out = tmp_path / "x"
    with tarfile.open(archive) as tar:
        tar.extractall(out, filter="data")
    (db,) = out.glob("*/var/experiments.sqlite")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT id FROM experiment").fetchall() == [("exp_1",)]


def _restore(state: dict[str, Path], archive: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(SCRIPTS / "restore.sh"), str(archive), *extra], env=_env(state))


def test_restore_verifies_refuses_a_newer_var_and_never_deletes(state: dict[str, Path]) -> None:
    archive = _backup(state)
    time.sleep(1.1)
    (state["var"] / "agents" / "audit.jsonl").write_text('{"seq": 1}\n{"seq": 2}\n')
    refused = _restore(state, archive)
    assert refused.returncode == 1 and "after the backup" in refused.stderr
    assert _restore(state, archive, "--dry-run").returncode == 1, "dry run still refuses"

    done = _restore(state, archive, "--force")
    assert done.returncode == 0, done.stderr + done.stdout
    assert (state["var"] / "agents" / "audit.jsonl").read_text() == '{"seq": 1}\n'
    (aside,) = state["var"].parent.glob("var.pre-restore-*")
    assert (aside / "agents" / "audit.jsonl").read_text().count("seq") == 2, "kept, not deleted"
    assert (state["var"] / "datastore" / "bars.parquet").exists(), "datastore carried over"
    assert (state["home"] / "env").read_text() == "FIBOKI_SESSION_SECRET=do-not-archive\n"


def test_restore_onto_an_empty_machine_needs_no_force(state: dict[str, Path]) -> None:
    archive = _backup(state)
    shutil.rmtree(state["var"])
    assert _restore(state, archive, "--dry-run").returncode == 0
    done = _restore(state, archive)
    assert done.returncode == 0, done.stderr
    with sqlite3.connect(state["var"] / "experiments.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM experiment").fetchone() == (1,)


def test_restore_refuses_a_damaged_archive(state: dict[str, Path]) -> None:
    archive = _backup(state)
    side = archive.with_name(archive.name + ".sha256")
    side.write_text("0" * 64 + f"  {archive.name}\n")
    proc = _restore(state, archive, "--dry-run")
    assert proc.returncode == 1 and "damaged or altered" in proc.stderr


# --------------------------------------------------- launcher model detection


class _FakeLlama(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        bodies = {
            "/props": {"default_generation_settings": {"n_ctx": 16384}, "model_path": "/m.gguf"},
            "/v1/models": {"object": "list", "data": [{"id": "qwen3-32b-q5_k_m"}]},
        }
        body = bodies.get(self.path)
        self.send_response(200 if body else 404)
        self.end_headers()
        self.wfile.write(json.dumps(body or {"error": "nope"}).encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def fake_llama() -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeLlama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _detection_block() -> str:
    text = (SCRIPTS / "desktop" / "Start Fiboki.command").read_text()
    start = text.index("# Optional local model.")
    end = text.index("scripts/llama-server.sh starts one)")
    end = text.index("\n", text.index("fi", end)) + 1
    return text[start:end]


def _detect(url: str, **env: str) -> dict[str, str]:
    if shutil.which("curl") is None or not Path("/usr/bin/python3").exists():
        pytest.skip("needs curl and /usr/bin/python3")
    script = "set -euo pipefail\n" + _detection_block() + "\nenv | grep '^FIBOKI_' | sort\n"
    proc = _run(["bash", "-c", script], env={"FIBOKI_AGENT_LOCAL_URL": url, **env})
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if line.startswith("FIBOKI_"))


def test_the_launcher_recognises_llama_cpp_and_names_its_model(fake_llama: str) -> None:
    env = _detect(fake_llama)
    assert env["FIBOKI_AGENT_PROVIDER"] == "local"
    assert env["FIBOKI_AGENT_LOCAL_URL"] == fake_llama
    assert env["FIBOKI_AGENT_LOCAL_MODEL"] == "qwen3-32b-q5_k_m"
    assert "FIBOKI_AGENT_CYCLES" not in env, "no target: cycles stay off, the worker survives"
    assert "FIBOKI_LLM_URL" not in env
    on = _detect(fake_llama, FIBOKI_AGENT_CYCLE_TARGET="donchian_breakout_atr:XAUUSD:H4")
    assert on["FIBOKI_AGENT_CYCLES"] == "true"
    pinned = _detect(fake_llama, FIBOKI_AGENT_LOCAL_MODEL="my-alias")
    assert pinned["FIBOKI_AGENT_LOCAL_MODEL"] == "my-alias", "an operator's choice is kept"


def test_the_launcher_with_no_server_leaves_agents_offline() -> None:
    env = _detect("http://127.0.0.1:9")
    assert "FIBOKI_AGENT_PROVIDER" not in env and "FIBOKI_AGENT_CYCLES" not in env


# ------------------------------------------------------- Fiboki.app launcher


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _fake_mac(bin_dir: Path, *, healthy_after: int, loaded: bool) -> None:
    """Stubs for the macOS commands the silent launcher touches. `curl` succeeds
    from the Nth call (each health probe is two calls); `launchctl print` says
    a label is loaded or not; `open`, `osascript` and `launchctl` record calls."""
    calls = bin_dir / "calls.log"
    _stub(bin_dir, "open", f'echo "open $*" >> {calls}')
    _stub(bin_dir, "osascript", f'echo "osascript $*" >> {calls}')
    _stub(bin_dir, "curl", (
        f'n=$(cat {bin_dir}/curl.count 2>/dev/null || echo 0); n=$((n+1)); echo $n > {bin_dir}/curl.count\n'
        f'[ $n -gt {healthy_after} ]'
    ))
    print_branch = 'echo "state = running"; exit 0' if loaded else "exit 113"
    _stub(bin_dir, "launchctl", (
        f'echo "launchctl $*" >> {calls}\n'
        'case "$1" in\n'
        f"  print) {print_branch} ;;\n"
        '  bootstrap|kickstart) exit 0 ;;\n'
        '  list) exit 0 ;;\n'
        'esac'
    ))


def _installed_launcher(tmp_path: Path) -> Path:
    desk = tmp_path / "Desktop"
    proc = _run(["bash", str(SCRIPTS / "desktop" / "install-launcher.sh"), "--desktop", str(desk)])
    assert proc.returncode == 0, proc.stderr
    return desk


def test_install_launcher_builds_a_silent_app_bundle(tmp_path: Path) -> None:
    desk = _installed_launcher(tmp_path)
    app = desk / "Fiboki.app" / "Contents"
    plist = plistlib.loads((app / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "Fiboki" and plist["LSUIElement"] is True
    assert plist["CFBundleIconFile"] == "Fiboki"
    exe = (app / "MacOS" / "Fiboki").read_text()
    assert "open -a Terminal" not in exe, "the app must not open a Terminal window"
    assert f'ROOT="${{FIBOKI_ROOT:-{REPO}}}"' in exe, "repository path substituted"
    assert "__FIBOKI_ROOT__" not in exe
    assert os.access(app / "MacOS" / "Fiboki", os.X_OK)
    assert (desk / "Start Fiboki (Terminal).command").exists()
    assert not (desk / "Start Fiboki.command").exists()
    # Idempotent, and honours --root.
    again = _run(["bash", str(SCRIPTS / "desktop" / "install-launcher.sh"), "--desktop", str(desk), "--root", str(REPO)])
    assert again.returncode == 0, again.stderr


def test_the_app_opens_the_workstation_when_already_healthy(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_mac(bin_dir, healthy_after=0, loaded=True)
    desk = _installed_launcher(tmp_path)
    proc = _run(["bash", str(desk / "Fiboki.app" / "Contents" / "MacOS" / "Fiboki")],
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    calls = (bin_dir / "calls.log").read_text()
    assert "open http://localhost:3000" in calls
    assert "launchctl bootstrap" not in calls and "display dialog" not in calls


def test_the_app_bootstraps_installed_services_then_waits_for_health(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Not loaded; plists installed under this HOME; healthy from the 3rd probe.
    _fake_mac(bin_dir, healthy_after=4, loaded=False)
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    for name in ("api", "worker", "web", "news"):
        (agents / f"uk.fiboki.{name}.plist").write_text("<plist/>")
    desk = _installed_launcher(tmp_path)
    proc = _run(["bash", str(desk / "Fiboki.app" / "Contents" / "MacOS" / "Fiboki")],
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}, timeout=60)
    assert proc.returncode == 0, proc.stderr
    calls = (bin_dir / "calls.log").read_text()
    for name in ("api", "worker", "web", "news"):
        assert f"launchctl bootstrap gui/{os.getuid()} {agents / f'uk.fiboki.{name}.plist'}" in calls
    assert "uk.fiboki.paper" not in calls, "paper only when its plist is installed"
    assert "display notification" in calls and "display dialog" not in calls
    assert calls.rstrip().endswith("open http://localhost:3000")
    log = (REPO / "var" / "logs" / "launcher.log").read_text()
    assert "healthy after" in log


def test_the_app_shows_a_dialog_when_the_platform_never_comes_up(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_mac(bin_dir, healthy_after=10_000, loaded=True)
    _stub(bin_dir, "sleep", "exit 0")  # the 120 s wait, without waiting
    desk = _installed_launcher(tmp_path)
    proc = _run(["bash", str(desk / "Fiboki.app" / "Contents" / "MacOS" / "Fiboki")],
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}, timeout=60)
    assert proc.returncode == 1
    calls = (bin_dir / "calls.log").read_text()
    assert "display dialog" in calls and "did not become healthy" in calls
    assert "open http://localhost:3000" not in calls
