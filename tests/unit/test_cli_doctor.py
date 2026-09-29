"""``fiboki doctor``: the desktop readiness check, against a fake host.

Every check reads the machine through :class:`fiboki.cli.DoctorHost`; these
tests replace it so each verdict is driven by a known observation. The local
model check runs the real llama.cpp provider over a recorded transport.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

from fiboki import cli
from fiboki.agents import providers as providers_module
from fiboki.cli import DoctorCheck, DoctorHost, DoctorStatus, main, run_doctor

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
PINS = {
    "numpy": "2.2.6", "pandas": "2.2.3", "scipy": "1.14.1", "pyarrow": "18.1.0",
    "click": "8.1.8", "typer": "0.15.1",
}


class FakeHost(DoctorHost):
    def __init__(self, repo: Path, env: dict[str, str] | None = None, **kw: Any) -> None:
        super().__init__(repo=repo, env=env or {"FIBOKI_EXECUTION_MODE": "paper"})
        self.system = kw.get("system", "Darwin")
        self.machine = kw.get("machine", "arm64")
        self.python_version = kw.get("python_version", (3, 11, 11))
        self.prefix = str(repo / ".venv")
        self.executable = str(repo / ".venv" / "bin" / "python")
        self.uid = 501
        self.commands: dict[tuple[str, ...], tuple[int, str]] = dict(kw.get("commands", {}))
        self.versions: dict[str, str | None] = dict(kw.get("versions", PINS))
        self.free = kw.get("free", 500 * 1024**3)
        self.open_ports: set[int] = set(kw.get("open_ports", ()))
        self.routes: dict[str, list[Any]] = dict(kw.get("routes", {}))
        self.clock = kw.get("now", NOW)

    def run(self, argv, timeout: float = 10.0):  # type: ignore[override]
        key = tuple(argv)
        if key in self.commands:
            return self.commands[key]
        if key[0] == "lsof":
            return 1, ""  # nothing listening
        if key[0] == "launchctl":
            return 113, "Could not find service"
        return 127, ""

    def dist_version(self, name: str) -> str | None:
        return self.versions.get(name)

    def disk_free(self, path: Path) -> int:
        return self.free

    def port_open(self, port: int) -> bool:
        return port in self.open_ports

    def http_client(self) -> Any:
        routes = {k: list(v) for k, v in self.routes.items()}

        def handler(request: httpx.Request) -> httpx.Response:
            queue = routes.get(f"{request.method} {request.url.host}:{request.url.port}{request.url.path}")
            if not queue:
                raise httpx.ConnectError("refused")
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            status, body = item if isinstance(item, tuple) else (200, item)
            return httpx.Response(status, json=body)

        return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)

    def now(self) -> datetime:
        return self.clock


@pytest.fixture(autouse=True)
def _no_digest_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_module, "_GGUF_DIGESTS", {})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "Fiboki"
    root.mkdir()
    deps = ",\n".join(f'  "{n}=={v}"' for n, v in PINS.items())
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "fiboki"\nversion = "2.0.0"\ndependencies = [\n{deps}\n]\n'
    )
    base = tmp_path / "python-home"
    base.mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("")
    (root / ".venv" / "pyvenv.cfg").write_text(f"home = {base}\nversion_info = 3.11.11.final.0\n")
    web = root / "apps" / "web" / "node_modules"
    (web / "@next" / "swc-darwin-arm64").mkdir(parents=True)
    (web / "next").mkdir(parents=True)
    (web / "next" / "package.json").write_text(json.dumps({"engines": {"node": ">=20.9.0"}}))
    return root


def _one(checks: list[DoctorCheck], name: str) -> DoctorCheck:
    (hit,) = [c for c in checks if c.name == name]
    return hit


def _run(host: DoctorHost, *names: str, **kw: Any) -> list[DoctorCheck]:
    return run_doctor(host, only=list(names), **kw)


# ------------------------------------------------------------- toolchain


def test_python_and_exact_pins(repo: Path) -> None:
    checks = _run(FakeHost(repo), "python")
    assert _one(checks, "python").status == DoctorStatus.OK
    pins = _one(checks, "pins")
    assert pins.status == DoctorStatus.OK and "numpy 2.2.6" in pins.detail

    drift = FakeHost(repo, versions={**PINS, "numpy": "2.3.0", "click": None})
    pins = _one(_run(drift, "python"), "pins")
    assert pins.status == DoctorStatus.FAIL
    assert "numpy 2.3.0 != 2.2.6" in pins.detail and "MISSING click" in pins.detail

    old = _one(_run(FakeHost(repo, python_version=(3, 13, 0)), "python"), "python")
    assert old.status == DoctorStatus.FAIL


def test_a_venv_copied_from_another_machine_fails(repo: Path) -> None:
    assert _one(_run(FakeHost(repo), ".venv"), ".venv").status == DoctorStatus.OK
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /Users/joe-macbook/nowhere\nversion = 3.11.9\n")
    check = _one(_run(FakeHost(repo), ".venv"), ".venv")
    assert check.status == DoctorStatus.FAIL and "does not exist" in check.detail
    assert "desktop-install.sh" in check.fix


def test_node_version_is_checked_against_the_installed_next_engines(repo: Path) -> None:
    ok = FakeHost(repo, commands={("node", "--version"): (0, "v22.11.0\n"),
                                  ("npm", "--version"): (0, "10.9.0\n")})
    assert _one(_run(ok, "node/npm"), "node/npm").status == DoctorStatus.OK
    old = FakeHost(repo, commands={("node", "--version"): (0, "v18.20.0\n"),
                                   ("npm", "--version"): (0, "10.1.0\n")})
    check = _one(_run(old, "node/npm"), "node/npm")
    assert check.status == DoctorStatus.FAIL and ">=20.9.0" in check.detail
    assert _one(_run(FakeHost(repo), "node/npm"), "node/npm").status == DoctorStatus.FAIL


def test_a_linux_built_node_modules_on_macos_fails(repo: Path) -> None:
    assert _one(_run(FakeHost(repo), "web node_modules"), "web node_modules").status == DoctorStatus.OK
    nm = repo / "apps" / "web" / "node_modules" / "@next"
    (nm / "swc-darwin-arm64").rmdir()
    (nm / "swc-linux-x64-gnu").mkdir()
    (nm / "swc-linux-x64-musl").mkdir()
    check = _one(_run(FakeHost(repo), "web node_modules"), "web node_modules")
    assert check.status == DoctorStatus.FAIL
    assert "another platform" in check.detail and "npm ci" in check.fix
    linux = FakeHost(repo, system="Linux", machine="x86_64")
    assert _one(_run(linux, "web node_modules"), "web node_modules").status == DoctorStatus.OK


def test_git_reports_branch_head_and_dirt(repo: Path) -> None:
    git = ("git", "-C", str(repo.resolve()))
    host = FakeHost(repo, commands={
        (*git, "rev-parse", "--abbrev-ref", "HEAD"): (0, "v2/integration\n"),
        (*git, "rev-parse", "--short", "HEAD"): (0, "a10d428\n"),
        (*git, "status", "--porcelain"): (0, " M src/x.py\n?? y\n"),
    })
    check = _one(_run(host, "git"), "git")
    assert check.status == DoctorStatus.WARN and "a10d428" in check.detail
    assert check.data == {"branch": "v2/integration", "head": "a10d428", "dirty": 2}


# ------------------------------------------------------------ environment


def test_unknown_env_warns_in_paper_and_missing_names_fail_in_demo(repo: Path) -> None:
    host = FakeHost(repo, env={"FIBOKI_EXECUTION_MODE": "paper", "FIBOKI_LLM_URL": "x"})
    check = _one(_run(host, "environment"), "environment")
    assert check.status == DoctorStatus.WARN and "FIBOKI_LLM_URL" in check.detail
    demo = FakeHost(repo, env={"FIBOKI_EXECUTION_MODE": "demo"})
    check = _one(_run(demo, "environment"), "environment")
    assert check.status == DoctorStatus.FAIL
    assert "FIBOKI_SESSION_SECRET" in check.data["missing_in_mode"]


def test_any_live_control_fails(repo: Path) -> None:
    clean = _one(_run(FakeHost(repo), "environment"), "live controls")
    assert clean.status == DoctorStatus.OK
    for env in ({"FIBOKI_LIVE_EXECUTION_ENABLED": "true"}, {"FIBOKI_LIVE_RUNTIME_ARMED": "t"}):
        check = _one(_run(FakeHost(repo, env=env), "environment"), "live controls")
        assert check.status == DoctorStatus.FAIL


# -------------------------------------------------------------- state


def test_data_root_must_be_marked_and_reports_bars_per_instrument(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fiboki.data.store import DataStore

    root = repo / "var" / "datastore"
    root.mkdir(parents=True)
    check = _one(_run(FakeHost(repo), "data root"), "data root")
    assert check.status == DoctorStatus.FAIL and ".fiboki-data-root" in check.detail
    DataStore.initialise(root)
    assert _one(_run(FakeHost(repo), "data root"), "data root").status == DoctorStatus.WARN
    frame = pd.DataFrame({"instrument": ["EURUSD", "EURUSD", "XAUUSD"], "rows": [100, 50, 7]})
    monkeypatch.setattr(DataStore, "inventory", lambda self: frame)
    check = _one(_run(FakeHost(repo), "data root"), "data root")
    assert check.status == DoctorStatus.OK
    assert check.data["bars_per_instrument"] == {"EURUSD": 150, "XAUUSD": 7}


def test_the_experiment_ledger_and_paper_journal(repo: Path) -> None:
    host = FakeHost(repo)
    assert _one(_run(host, "experiment ledger"), "experiment ledger").status == DoctorStatus.FAIL
    db = repo / "var" / "experiments.sqlite"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE experiment (id TEXT)")
        conn.executemany("INSERT INTO experiment VALUES (?)", [("a",), ("b",)])
    check = _one(_run(host, "experiment ledger"), "experiment ledger")
    assert check.status == DoctorStatus.OK and check.data == {"experiments": 2}

    assert _one(_run(host, "paper journal"), "paper journal").status == DoctorStatus.WARN
    session = repo / "var" / "paper" / "s1"
    session.mkdir(parents=True)
    (session / "summary.json").write_text("{}")
    assert _one(_run(host, "paper journal"), "paper journal").data == {"sessions": 1}


def _beat(state_db: Path, at: datetime) -> None:
    from fiboki.workers.base import WORKER_HEARTBEAT, WorkerStore

    store = WorkerStore.sqlite_at(state_db)
    with store.engine.begin() as conn:
        conn.execute(WORKER_HEARTBEAT.insert().values(
            worker_id="research@mac:1", kind="research", beat_at=at, started_at=at))
    store.close()


def test_worker_heartbeat_age_and_a_null_age_is_not_zero(repo: Path, tmp_path: Path) -> None:
    state_db = tmp_path / "home" / "state.db"
    env = {"FIBOKI_EXECUTION_MODE": "paper", "FIBOKI_STATE_DB": str(state_db)}
    check = _one(_run(FakeHost(repo, env=env), "worker heartbeat"), "worker heartbeat")
    assert check.status == DoctorStatus.FAIL and "no worker has ever beaten" in check.detail
    assert not state_db.exists(), "the doctor must not create the state database"
    state_db.parent.mkdir(parents=True)
    _beat(state_db, NOW - timedelta(seconds=30))
    check = _one(_run(FakeHost(repo, env=env), "worker heartbeat"), "worker heartbeat")
    assert check.status == DoctorStatus.OK and "30s ago" in check.detail
    late = FakeHost(repo, env=env, now=NOW + timedelta(minutes=10))
    assert _one(_run(late, "worker heartbeat"), "worker heartbeat").status == DoctorStatus.FAIL


def test_worker_heartbeat_threshold_is_settings_health(repo: Path, tmp_path: Path) -> None:
    """The doctor reads Settings.health, not its own 120 s literal."""
    state_db = tmp_path / "home" / "state.db"
    state_db.parent.mkdir(parents=True)
    _beat(state_db, NOW - timedelta(seconds=200))
    env = {"FIBOKI_EXECUTION_MODE": "paper", "FIBOKI_STATE_DB": str(state_db)}
    default = _one(_run(FakeHost(repo, env=env), "worker heartbeat"), "worker heartbeat")
    assert default.status == DoctorStatus.FAIL and "stale after 120s" in default.detail
    wide = FakeHost(repo, env={**env, "FIBOKI_WORKER_STALE_SECONDS": "300",
                               "FIBOKI_WORKER_DOWN_SECONDS": "600"})
    check = _one(_run(wide, "worker heartbeat"), "worker heartbeat")
    assert check.status == DoctorStatus.OK and "stale after 300s" in check.detail
    bad = FakeHost(repo, env={**env, "FIBOKI_WORKER_STALE_SECONDS": "-5"})
    check = _one(_run(bad, "worker heartbeat"), "worker heartbeat")
    assert check.status == DoctorStatus.FAIL and "check crashed" in check.detail


def test_legacy_sha256_operator_hashes_are_flagged(repo: Path) -> None:
    from fiboki.api.routers.auth import hash_password

    legacy = hashlib.sha256(b"pw").hexdigest()
    scrypt = hash_password("pw")
    env = {"FIBOKI_EXECUTION_MODE": "paper",
           "FIBOKI_OPERATORS": f"joe:admin:{scrypt},tom:admin:{legacy}"}
    check = _one(_run(FakeHost(repo, env=env), "operator hashes"), "operator hashes")
    assert check.status == DoctorStatus.WARN
    assert "tom" in check.detail and "joe" not in check.detail
    assert legacy not in check.detail and legacy not in json.dumps(check.as_dict())
    assert check.data == {"operators": 2, "legacy": ["tom"], "malformed": 0}

    env["FIBOKI_OPERATORS"] = f"joe:admin:{scrypt},tom:operator:{hash_password('x')}"
    check = _one(_run(FakeHost(repo, env=env), "operator hashes"), "operator hashes")
    assert check.status == DoctorStatus.OK and "2 operator(s), all scrypt" in check.detail
    unset = _one(_run(FakeHost(repo), "operator hashes"), "operator hashes")
    assert unset.status == DoctorStatus.WARN and "not set" in unset.detail


def test_news_store_last_poll(repo: Path) -> None:
    host = FakeHost(repo)
    assert _one(_run(host, "news store"), "news store").status == DoctorStatus.WARN
    store = repo / "var" / "news" / "headlines.sqlite"
    store.parent.mkdir(parents=True)
    with sqlite3.connect(store) as conn:
        conn.execute("CREATE TABLE poll_log (id INTEGER PRIMARY KEY, started_at TEXT, "
                     "finished_at TEXT, outcomes_json TEXT)")
        conn.execute("INSERT INTO poll_log (started_at) VALUES (?)",
                     ((NOW - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),))
    check = _one(_run(host, "news store"), "news store")
    assert check.status == DoctorStatus.OK and check.data["age_seconds"] == 240.0
    stale = FakeHost(repo, now=NOW + timedelta(hours=1))
    assert _one(_run(stale, "news store"), "news store").status == DoctorStatus.WARN


def test_calendar_coverage_end_date(repo: Path) -> None:
    from fiboki.marketstate.calendar import load_official_calendar

    end = load_official_calendar().coverage().declared_end.to_pydatetime()
    assert _one(_run(FakeHost(repo, now=end - timedelta(days=90)), "calendar coverage"),
                "calendar coverage").status == DoctorStatus.OK
    assert _one(_run(FakeHost(repo, now=end - timedelta(days=5)), "calendar coverage"),
                "calendar coverage").status == DoctorStatus.WARN
    past = _one(_run(FakeHost(repo, now=end + timedelta(days=1)), "calendar coverage"),
                "calendar coverage")
    assert past.status == DoctorStatus.FAIL and "trade through events" in past.fix


# -------------------------------------------------------------- local model


def _llama_routes(gguf: Path) -> dict[str, list[Any]]:
    return {
        "GET 127.0.0.1:8080/v1/models": [{"object": "list", "data": [
            {"id": "qwen3-14b", "object": "model", "owned_by": "llamacpp", "meta": {}}]}],
        "GET 127.0.0.1:8080/props": [{
            "default_generation_settings": {"n_ctx": 16384, "params": {}},
            "total_slots": 1, "model_path": str(gguf), "build_info": "b6358-c466abe1"}],
    }


def test_local_model_llama_cpp_is_found_and_pinned(repo: Path, tmp_path: Path) -> None:
    gguf = tmp_path / "Models" / "q.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"GGUF-weights")
    env = {"FIBOKI_EXECUTION_MODE": "paper", "FIBOKI_HOME": str(tmp_path / "home")}
    check = _one(_run(FakeHost(repo, env=env, routes=_llama_routes(gguf)), "local model"),
                 "local model")
    assert check.status == DoctorStatus.OK, check
    assert check.data["backend"] == "llama.cpp" and check.data["context"] == 16384
    assert check.data["digest"] == "sha256:" + hashlib.sha256(b"GGUF-weights").hexdigest()
    assert (tmp_path / "home" / "gguf-digests.json").exists(), "digest cached under FIBOKI_HOME"

    skipped = _run(FakeHost(repo, env=env, routes=_llama_routes(gguf)), "local model",
                   hash_weights=False)
    assert _one(skipped, "local model").status == DoctorStatus.WARN


def test_local_model_wanted_but_the_runtime_cannot_drive_llama_cpp_fails(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gguf = tmp_path / "q.gguf"
    gguf.write_bytes(b"w")
    env = {"FIBOKI_EXECUTION_MODE": "paper", "FIBOKI_HOME": str(tmp_path / "home"),
           "FIBOKI_AGENT_PROVIDER": "local", "FIBOKI_AGENT_LOCAL_URL": "http://127.0.0.1:8080",
           "FIBOKI_AGENT_LOCAL_MODEL": "qwen3-14b"}
    monkeypatch.setattr(cli, "_runtime_drives_llama_cpp", lambda: False)
    check = _one(_run(FakeHost(repo, env=env, routes=_llama_routes(gguf)), "local model"),
                 "local model")
    assert check.status == DoctorStatus.FAIL and "for_ollama" in check.fix
    monkeypatch.setattr(cli, "_runtime_drives_llama_cpp", lambda: True)
    check = _one(_run(FakeHost(repo, env=env, routes=_llama_routes(gguf)), "local model"),
                 "local model")
    assert check.status == DoctorStatus.OK

    no_name = {k: v for k, v in env.items() if k != "FIBOKI_AGENT_LOCAL_MODEL"}
    check = _one(_run(FakeHost(repo, env=no_name, routes=_llama_routes(gguf)), "local model"),
                 "local model")
    assert check.status == DoctorStatus.FAIL
    assert check.fix == "export FIBOKI_AGENT_LOCAL_MODEL=qwen3-14b"


def test_no_model_server_is_a_warning_unless_the_worker_needs_one(repo: Path) -> None:
    check = _one(_run(FakeHost(repo), "local model"), "local model")
    assert check.status == DoctorStatus.WARN and "no local model server" in check.detail
    wanted = FakeHost(repo, env={"FIBOKI_AGENT_PROVIDER": "local"})
    assert _one(_run(wanted, "local model"), "local model").status == DoctorStatus.FAIL


def test_the_runtime_probe_reads_the_real_composition_root() -> None:
    from fiboki.workers import research_runtime

    source = Path(research_runtime.__file__).read_text()
    assert cli._runtime_drives_llama_cpp() == ("for_local_server(" in source
                                               or "for_llama_cpp(" in source)


# ------------------------------------------------------ disk, ports, launchd


def test_disk_free_thresholds(repo: Path) -> None:
    assert _one(_run(FakeHost(repo), "disk free"), "disk free").status == DoctorStatus.OK
    assert _one(_run(FakeHost(repo, free=10 * 1024**3), "disk free"),
                "disk free").status == DoctorStatus.WARN
    assert _one(_run(FakeHost(repo, free=1024**3), "disk free"), "disk free").status == DoctorStatus.FAIL


def test_ports_free_owned_by_fiboki_or_held_by_something_else(repo: Path) -> None:
    lsof = ("lsof", "-nP")
    host = FakeHost(repo, commands={
        (*lsof, "-iTCP:8000", "-sTCP:LISTEN", "-Fp"): (0, "p4242\n"),
        ("ps", "-o", "command=", "-p", "4242"): (0, f"{repo}/.venv/bin/python -m uvicorn "
                                                    "fiboki.api.app:asgi_factory\n"),
        (*lsof, "-iTCP:3000", "-sTCP:LISTEN", "-Fp"): (0, "p77\n"),
        ("ps", "-o", "command=", "-p", "77"): (0, "/usr/bin/some-other-dev-server\n"),
    })
    checks = _run(host, "ports")
    assert _one(checks, "port 8000 (api)").status == DoctorStatus.OK
    assert "owned by Fiboki" in _one(checks, "port 8000 (api)").detail
    held = _one(checks, "port 3000 (web)")
    assert held.status == DoctorStatus.FAIL and "pid 77" in held.detail
    assert _one(checks, "port 8080 (llama-server)").detail == "free"


def test_ports_without_lsof_fall_back_to_a_connect_probe(repo: Path) -> None:
    class NoLsof(FakeHost):
        def run(self, argv, timeout: float = 10.0):  # type: ignore[override]
            return (127, "") if argv[0] == "lsof" else super().run(argv, timeout)

    checks = _run(NoLsof(repo, open_ports={8080}), "ports")
    assert _one(checks, "port 8080 (llama-server)").status == DoctorStatus.WARN
    assert _one(checks, "port 8000 (api)").status == DoctorStatus.OK


def test_launchd_state_per_service(repo: Path) -> None:
    running = (0, "\tstate = running\n\tpid = 1\n\tlast exit code = (never exited)\n")
    labels = [f"gui/501/uk.fiboki.{n}" for n in ("api", "worker", "web", "news", "llama")]
    host = FakeHost(repo, commands={("launchctl", "print", lbl): running for lbl in labels})
    check = _one(_run(host, "launchd"), "launchd")
    assert check.status == DoctorStatus.OK and "worker: running" in check.detail

    partial = FakeHost(repo, commands={("launchctl", "print", labels[0]): running})
    check = _one(_run(partial, "launchd"), "launchd")
    assert check.status == DoctorStatus.WARN and "news: not loaded" in check.detail

    both = FakeHost(repo, commands={
        **{("launchctl", "print", lbl): running for lbl in labels},
        ("launchctl", "print", "gui/501/com.fiboki.research-worker"): running,
    })
    assert "com.fiboki.research-worker" in _one(_run(both, "launchd"), "launchd").fix
    linux = FakeHost(repo, system="Linux")
    assert _one(_run(linux, "launchd"), "launchd").status == DoctorStatus.WARN


def test_launchd_lists_the_optional_paper_service(repo: Path) -> None:
    running = (0, "\tstate = running\n\tpid = 1\n\tlast exit code = (never exited)\n")
    core = [f"gui/501/uk.fiboki.{n}" for n in ("api", "worker", "web", "news", "llama")]
    host = FakeHost(repo, commands={("launchctl", "print", lbl): running for lbl in core})
    check = _one(_run(host, "launchd"), "launchd")
    assert "uk.fiboki.paper" in cli.DOCTOR_LAUNCHD_LABELS
    assert check.status == DoctorStatus.OK and "paper: not loaded (optional)" in check.detail
    assert check.data["uk.fiboki.paper"] == "not loaded"

    exited = (0, "\tstate = not running\n\tlast exit code = 75\n")
    host = FakeHost(repo, commands={
        **{("launchctl", "print", lbl): running for lbl in core},
        ("launchctl", "print", "gui/501/uk.fiboki.paper"): exited,
    })
    check = _one(_run(host, "launchd"), "launchd")
    assert check.status == DoctorStatus.WARN and "paper: not running, last exit 75" in check.detail


# ------------------------------------------------------------ the command


def test_a_crashing_check_is_a_fail_row_not_a_traceback(repo: Path, monkeypatch: pytest.MonkeyPatch
                                                        ) -> None:
    def boom(host: DoctorHost) -> list[DoctorCheck]:
        raise RuntimeError("kaput")

    monkeypatch.setattr(cli, "DOCTOR_CHECKS", (("git", boom),))
    (check,) = run_doctor(FakeHost(repo))
    assert check.status == DoctorStatus.FAIL and "kaput" in check.detail


def test_every_check_runs_on_a_fake_host_and_json_counts_add_up(repo: Path) -> None:
    checks = run_doctor(FakeHost(repo), hash_weights=False)
    names = {c.name for c in checks}
    for expected in ("python", "pins", ".venv", "node/npm", "web node_modules", "git",
                     "environment", "live controls", "operator hashes", "data root", "experiment ledger",
                     "paper journal", "worker heartbeat", "news store", "calendar coverage",
                     "local model", "disk free", "port 8000 (api)", "launchd"):
        assert expected in names
    assert not [c for c in checks if "check crashed" in c.detail]
    payload = cli._doctor_payload(checks)
    assert sum(payload["counts"].values()) == len(checks)
    assert payload["ok"] is (payload["counts"]["FAIL"] == 0)
    json.dumps(payload)


def test_the_command_exits_non_zero_on_fail_and_emits_json(repo: Path, capsys) -> None:
    assert main(["doctor", "--json", "--only", "python", "--repo", str(REPO)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {c["name"] for c in payload["checks"]} == {"python", "pins"}
    assert main(["doctor", "--json", "--only", "data root", "--repo", str(repo)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["FAIL"] == 1


def test_doctor_is_a_group_with_a_model_subcommand() -> None:
    import typer.main

    command = typer.main.get_command(cli.app)
    assert "model" in command.commands["doctor"].commands  # type: ignore[attr-defined]
