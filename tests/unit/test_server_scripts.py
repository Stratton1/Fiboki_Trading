"""The Linux server deployment: units, installer, heartbeat, backup and the Mac-to-server migration.

Like test_desktop_scripts.py, nothing here touches the machine running the tests: scripts are pointed at
``tmp_path`` and every external program (systemctl, curl, fiboki, ssh, launchctl, rclone) is a stub on
PATH that records its arguments. What cannot be exercised here (apt, ufw, a real systemd, a real
server) is said so in docs/v2/SERVER.md, not faked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
UNITS = REPO / "deploy" / "systemd"
SERVICES = ("api", "worker", "web", "news", "quotes", "paper", "paper-crypto", "llama")
SERVER_SCRIPTS = ("server-install.sh", "server-migrate.sh", "server-backup.sh", "server-heartbeat.sh",
                  "fiboki-service.sh", "llama-server.sh")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(argv: list[str], env: dict[str, str] | None = None, cwd: Path = REPO,
         timeout: float = 120, inherit: bool = True) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("FIBOKI_", "FIBOKEI_"))} if inherit else {}
    return subprocess.run(argv, env={**base, **(env or {})}, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _bytes(text: str) -> int:
    m = re.fullmatch(r"(\d+)([KMG]?)", text.strip())
    assert m, text
    return int(m[1]) * {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}[m[2]]


def _directives(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or "=" not in line or line.startswith("["):
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("units")
    proc = _run(["bash", str(SCRIPTS / "server-install.sh"), "--render-units", str(out)])
    assert proc.returncode == 0, proc.stderr
    return out


# ------------------------------------------------------------------ scripts in general


@pytest.mark.parametrize("name", [*SERVER_SCRIPTS])
def test_every_server_script_parses_and_sets_no_live_control(name: str) -> None:
    path = SCRIPTS / name
    assert _run(["bash", "-n", str(path)]).returncode == 0
    text = path.read_text()
    for flag in ("FIBOKI_LIVE_EXECUTION_ENABLED", "FIBOKI_LIVE_RUNTIME_ARMED", "FIBOKI_OANDA_LIVE_RUNTIME"):
        assert not re.search(rf"(export\s+)?{flag}=", text), f"{name} assigns {flag}"


def test_no_server_file_embeds_a_secret_or_a_live_flag() -> None:
    """Units and scripts name secrets (they are read from /etc/fiboki/env), they never contain one."""
    files = [*UNITS.glob("fiboki*"), *(SCRIPTS / n for n in SERVER_SCRIPTS), SCRIPTS / "migrate_preflight.py"]
    for path in files:
        text = path.read_text()
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"(TOKEN|SECRET|PASSWORD|PRIVATE_KEY|API_KEY)\w*=[^\s$\"'#]{8,}", line), (path.name, line)
            assert "LIVE_EXECUTION_ENABLED" not in line or "unset" in line or "assigns" in line, (path.name, line)
        assert not re.search(r"\b[0-9a-f]{64}\b", text) or path.name == "llama-server.sh", \
            f"{path.name} holds a 64-hex string (only the pinned model digests may)"


# ---------------------------------------------------------------------------------- units


def test_every_service_has_a_unit_that_runs_the_wrapper_as_the_service_user(rendered: Path) -> None:
    for name in SERVICES:
        unit = _directives(rendered / f"fiboki-{name}.service")
        assert unit["ExecStart"] == [f"/bin/bash /opt/fiboki/scripts/fiboki-service.sh {name}"]
        assert unit["User"] == ["fiboki"] and unit["Group"] == ["fiboki"]
        assert unit["Restart"] == ["on-failure"]
        assert unit["StartLimitBurst"] and unit["StartLimitIntervalSec"]
        assert unit["Environment"].count("FIBOKI_EXECUTION_MODE=paper") == 1
        assert "FIBOKI_ENV_FILE=/etc/fiboki/env" in unit["Environment"]
        assert unit["WorkingDirectory"] == ["/opt/fiboki"]
        assert unit["Slice"] == ["fiboki.slice"]
        text = (rendered / f"fiboki-{name}.service").read_text()
        assert "__FIBOKI_" not in text
        assert "WantedBy=multi-user.target" in text


def test_every_unit_is_hardened_and_can_only_write_the_state_directory(rendered: Path) -> None:
    for name in SERVICES:
        unit = _directives(rendered / f"fiboki-{name}.service")
        assert unit["NoNewPrivileges"] == ["true"]
        assert unit["ProtectSystem"] == ["strict"]
        assert unit["ProtectHome"] == ["true"]
        assert unit["PrivateTmp"] == ["true"]
        assert unit["PrivateDevices"] == ["true"]
        writable = " ".join(unit["ReadWritePaths"])
        assert "/var/lib/fiboki" in writable
        assert "/etc" not in writable and "/opt/fiboki " not in writable + " ", "code and config stay read-only"
        assert unit["RestrictAddressFamilies"], name
        assert unit["MemoryMax"], f"{name} has no memory cap"


def test_only_the_web_app_may_write_inside_the_code_tree_and_only_its_cache(rendered: Path) -> None:
    web = _directives(rendered / "fiboki-web.service")
    assert "-/opt/fiboki/apps/web/.next/cache" in web["ReadWritePaths"]
    assert web["RestartPreventExitStatus"] == ["78"], "no build is not a crash loop"
    assert web["KillMode"] == ["control-group"], "npm must not leave next-server behind"
    for name in SERVICES:
        if name != "web":
            assert all("/opt/fiboki" not in p for p in _directives(rendered / f"fiboki-{name}.service")["ReadWritePaths"])


def test_exit_75_is_a_clean_stop_and_stop_timeouts_exceed_the_workers_grace(rendered: Path) -> None:
    for name in ("worker", "paper", "paper-crypto", "api", "web", "news", "quotes", "llama"):
        unit = _directives(rendered / f"fiboki-{name}.service")
        assert unit["SuccessExitStatus"] == ["75"], name
    for name in ("worker", "paper", "paper-crypto"):
        assert int(_directives(rendered / f"fiboki-{name}.service")["TimeoutStopSec"][0]) > 30, \
            "WorkerConfig.shutdown_grace_seconds is 30"


def test_the_memory_budget_cannot_exhaust_a_16_gb_machine(rendered: Path) -> None:
    caps = {n: _bytes(_directives(rendered / f"fiboki-{n}.service")["MemoryMax"][0]) for n in SERVICES}
    high = {n: _bytes(_directives(rendered / f"fiboki-{n}.service")["MemoryHigh"][0]) for n in SERVICES}
    assert all(high[n] < caps[n] for n in SERVICES), "MemoryHigh throttles before MemoryMax kills"
    slice_ = _directives(rendered / "fiboki.slice")
    group = _bytes(slice_["MemoryMax"][0])
    assert group <= 14 * 1024**3, "leave at least 2 GB of 16 for the OS, sshd, tailscaled and the page cache"
    assert _bytes(slice_["MemoryHigh"][0]) < group
    # The sizes the brief asks for: paper, quotes and news small; api and web medium; worker and llama large.
    small = max(caps[n] for n in ("paper", "paper-crypto", "quotes", "news"))
    medium = max(caps["api"], caps["web"])
    assert small <= medium < caps["worker"] < caps["llama"]
    # Everything that runs by default fits in the group even at every cap.
    assert sum(caps[n] for n in ("api", "worker", "web", "news", "paper", "paper-crypto", "quotes")) <= group
    # Who dies first when memory runs out: the model and the sweep, never the paper sessions.
    oom = {n: int(_directives(rendered / f"fiboki-{n}.service")["OOMScoreAdjust"][0]) for n in SERVICES}
    assert oom["llama"] > oom["worker"] > 0 > oom["paper"] and oom["paper"] == oom["paper-crypto"] < oom["api"]


def test_the_research_worker_yields_the_machine(rendered: Path) -> None:
    worker = _directives(rendered / "fiboki-worker.service")
    assert worker["Nice"] == ["10"] and worker["IOSchedulingClass"] == ["idle"] and worker["CPUWeight"] == ["50"]
    assert worker["Conflicts"] == ["fiboki-research-worker.service"], "two workers would fight over one lease"
    assert _directives(rendered / "fiboki-llama.service")["CPUQuota"] == ["400%"]
    for name in ("paper", "paper-crypto"):
        assert _directives(rendered / f"fiboki-{name}.service")["Nice"] == ["0"]


def test_the_timers_and_oneshots_exist_and_the_heartbeat_sits_outside_the_slice(rendered: Path) -> None:
    backup = _directives(rendered / "fiboki-backup.timer")
    assert backup["Persistent"] == ["true"] and backup["OnCalendar"] and backup["Unit"] == ["fiboki-backup.service"]
    beat = _directives(rendered / "fiboki-heartbeat.timer")
    assert beat["OnUnitActiveSec"] == ["5min"]
    bsvc = _directives(rendered / "fiboki-backup.service")
    assert bsvc["Type"] == ["oneshot"]
    assert "/var/backups/fiboki" in " ".join(bsvc["ReadWritePaths"])
    assert bsvc["ExecStart"] == ["/bin/bash /opt/fiboki/scripts/fiboki-service.sh run /opt/fiboki/scripts/server-backup.sh"]
    hsvc = _directives(rendered / "fiboki-heartbeat.service")
    assert "Slice" not in hsvc, "the heartbeat must still run when the Fiboki group is starved"
    assert hsvc["ExecStart"] == ["/bin/bash /opt/fiboki/scripts/fiboki-service.sh run /opt/fiboki/scripts/server-heartbeat.sh"]


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_the_units_pass_systemd_analyze_verify(rendered: Path) -> None:
    files = sorted(p.name for p in rendered.iterdir() if p.suffix in (".service", ".timer", ".slice"))
    proc = _run(["systemd-analyze", "verify", *files], cwd=rendered)
    complaints = [ln for ln in (proc.stdout + proc.stderr).splitlines()
                  if re.search(r"Unknown (key|section)|Invalid|Failed to parse|not a valid|ignoring", ln)]
    assert complaints == [], complaints


def test_the_installer_substitutes_a_different_prefix_and_refuses_unsafe_paths(tmp_path: Path) -> None:
    out = tmp_path / "u"
    proc = _run(["bash", str(SCRIPTS / "server-install.sh"), "--render-units", str(out),
                 "--prefix", "/srv/fb", "--user", "fb"])
    assert proc.returncode == 0, proc.stderr
    unit = _directives(out / "fiboki-api.service")
    assert unit["ExecStart"] == ["/bin/bash /srv/fb/scripts/fiboki-service.sh api"] and unit["User"] == ["fb"]
    ctl = (out / "fiboki-ctl").read_text()
    assert "sudo -u fb" in ctl and "/srv/fb/scripts/fiboki-service.sh run" in ctl
    bad = _run(["bash", str(SCRIPTS / "server-install.sh"), "--render-units", str(out), "--prefix", "/opt/a&b"])
    assert bad.returncode == 2 and "cannot handle" in bad.stderr


def test_the_legacy_single_unit_is_not_installed_and_docker_files_are_untouched(rendered: Path) -> None:
    assert not (rendered / "fiboki-research-worker.service").exists()
    assert (UNITS / "fiboki-research-worker.service").exists()
    assert (REPO / "deploy" / "Dockerfile").exists() and (REPO / "deploy" / "docker-compose.yml").exists()


# ----------------------------------------------------------------------- installer


linux_only = pytest.mark.skipif(sys.platform != "linux", reason="the installer is Linux-only")


@linux_only
def test_dry_run_prints_every_step_and_changes_nothing(tmp_path: Path) -> None:
    proc = _run(["bash", str(SCRIPTS / "server-install.sh"), "--dry-run"])
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for fragment in ("DRY RUN", "== 1  apt packages", "useradd --system", "git clone --branch main https://github.com/Stratton1/Fiboki_Trading.git /opt/fiboki",
                     "-m venv /opt/fiboki/.venv", "-c /opt/fiboki/deploy/constraints.txt", "npm ci", "npm run build",
                     "write /etc/fiboki/env (mode 600", "systemctl enable fiboki-api.service fiboki-worker.service fiboki-web.service fiboki-news.service fiboki-backup.timer fiboki-heartbeat.timer",
                     "ufw default deny incoming", "ufw limit 22/tcp", "ufw --force enable", "journald.conf.d/fiboki.conf", "SystemMaxUse=1G",
                     "/etc/logrotate.d/fiboki", "20auto-upgrades", "/swapfile", "systemctl start fiboki-api.service"):
        assert fragment in out, fragment
    assert not re.search(r"\b[0-9a-f]{64}\b", out), "no secret-looking value may be printed"
    assert "FIBOKI_SESSION_SECRET=" not in out
    assert "tailscale serve" not in out and "apt-get install tailscale" not in out, "Tailscale is opt-in"
    assert not Path("/etc/fiboki/env").exists() or os.geteuid() == 0


@linux_only
def test_services_flag_no_start_and_the_tailscale_option(tmp_path: Path) -> None:
    proc = _run(["bash", str(SCRIPTS / "server-install.sh"), "--dry-run", "--services", "api,paper-crypto",
                 "--no-start", "--tailscale", "--no-firewall", "--no-swap", "--ssh-port", "2222"])
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "systemctl enable fiboki-api.service fiboki-paper-crypto.service fiboki-backup.timer" in out
    assert "fiboki-worker.service fiboki-web" not in out.split("systemctl enable", 1)[1].splitlines()[0]
    assert "--no-start: units are enabled, nothing is started" in out
    assert "tailscale serve --bg --https=443 http://127.0.0.1:3000" in out
    assert "funnel" not in out.replace("NEVER `tailscale funnel`", "")
    assert "ufw" not in out.replace("== 9  firewall (ufw)", "") and "skipped (--no-firewall)" in out
    assert "/swapfile" not in out
    firewall = _run(["bash", str(SCRIPTS / "server-install.sh"), "--dry-run", "--ssh-port", "2222", "--tailscale"])
    assert "ufw limit 2222/tcp" in firewall.stdout and "ufw allow in on tailscale0" in firewall.stdout


def test_the_installer_refuses_unknown_services_and_non_root_real_runs() -> None:
    bad = _run(["bash", str(SCRIPTS / "server-install.sh"), "--dry-run", "--services", "api,nope"])
    assert bad.returncode == 2 and "unknown service 'nope'" in bad.stderr
    if os.geteuid() != 0:
        real = _run(["bash", str(SCRIPTS / "server-install.sh")])
        assert real.returncode == 1 and "run as root" in real.stderr


def test_the_installer_wires_the_security_properties_the_brief_requires() -> None:
    text = (SCRIPTS / "server-install.sh").read_text()
    assert text.index("ufw limit") < text.index("ufw --force enable"), "SSH is allowed before the firewall is enabled"
    assert "ufw default deny incoming" in text
    assert "chmod 600 \"$ENV_FILE\"" in text and "openssl rand -hex 32" in text
    assert "tailscale funnel" not in text.replace("NEVER `tailscale funnel`", "")
    assert "| bash" not in text and "| sh" not in text, "no pipe-to-shell installs"
    assert "ss -H -ltn" in text, "it verifies nothing listens publicly"


# ------------------------------------------------------------- the service wrapper on Linux


def test_run_executes_a_command_under_the_server_environment(tmp_path: Path) -> None:
    env_file = tmp_path / "etc" / "env"
    env_file.parent.mkdir()
    env_file.write_text("FIBOKI_OPERATORS='joe:admin:scrypt$1$2'\nFIBOKI_EXECUTION_MODE=live\n"
                        "FIBOKI_LIVE_EXECUTION_ENABLED=true\nMARK=from-file\n")
    proc = _run(["bash", str(SCRIPTS / "fiboki-service.sh"), "run", "sh", "-c",
                 'echo "$MARK|$FIBOKI_OPERATORS|$FIBOKI_EXECUTION_MODE|${FIBOKI_LIVE_EXECUTION_ENABLED:-unset}|$FIBOKI_STATE_DIR"'],
                env={"FIBOKI_ENV_FILE": str(env_file), "FIBOKI_STATE_DIR": str(tmp_path / "state"),
                     "FIBOKI_HOME": str(tmp_path / "home"), "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    mark, ops, mode, live, state = proc.stdout.strip().split("|")
    assert (mark, ops, mode, live) == ("from-file", "joe:admin:scrypt$1$2", "paper", "unset")
    assert state == str(tmp_path / "state") and (tmp_path / "state" / "logs").is_dir()
    assert _run(["bash", str(SCRIPTS / "fiboki-service.sh"), "run"]).returncode == 2


def test_the_env_file_override_is_honoured_by_python_and_shell_alike(tmp_path: Path) -> None:
    from fiboki.core.env_file import env_file_path

    env = {"FIBOKI_ENV_FILE": str(tmp_path / "e"), "FIBOKI_HOME": str(tmp_path / "h")}
    assert env_file_path(env) == tmp_path / "e"
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    assert 'ENV_FILE="$FIBOKI_ENV_FILE"' in text and text.index('FIBOKI_ENV_FILE:-') < text.index('done < "$ENV_FILE"')


def test_the_web_service_binds_loopback_on_linux() -> None:
    text = (SCRIPTS / "fiboki-service.sh").read_text()
    assert 'WEB_HOST="127.0.0.1"' in text and 'npm run start -- -H "$WEB_HOST"' in text
    assert 'uname -s)" = "Linux"' in text, "macOS keeps Next's default; the desktop app is unchanged"
    api = text[text.index("  api)"):text.index("  worker)")]
    assert "--host 127.0.0.1" in api


# ---------------------------------------------------------------------- stubs for the rest


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    # curl: log the argv and what arrives on stdin (-K -), answer with the http code the test asks for.
    _stub(bin_dir, "curl", f'''
args="$*"
stdin=""
case "$args" in *"-K -"*) stdin="$(cat)";; esac
printf 'CURL %s\\n' "$args" >> {log}
[ -n "$stdin" ] && printf 'STDIN %s\\n' "$stdin" >> {log}
printf '%s' "${{STUB_HTTP:-200}}"
''')
    _stub(bin_dir, "systemctl", f'''
printf 'SYSTEMCTL %s\\n' "$*" >> {log}
case "$1" in
  is-enabled) case " $STUB_ENABLED " in *" $2 "*) echo enabled;; *) echo disabled; exit 1;; esac ;;
  is-active) case " $STUB_FAILED " in *" $2 "*) echo failed; exit 3;; *) echo active;; esac ;;
  list-unit-files) for u in $STUB_ENABLED; do case "$u" in *.service) echo "$u enabled enabled";; esac; done ;;
esac
exit 0
''')
    _stub(bin_dir, "fiboki", f'''
printf 'FIBOKI %s\\n' "$*" >> {log}
case "$1 $2" in
  "doctor --json") cat "$STUB_DOCTOR" ;;
  "worker status") cat "$STUB_WORKERS" ;;
  "quotes status") [ "${{STUB_QUOTES_RC:-0}}" = 0 ] && echo "fresh" || {{ echo "recorder is not writing (newest write 4000s ago)"; exit 1; }} ;;
esac
''')
    _stub(bin_dir, "hostname", "echo testbox")
    _stub(bin_dir, "date", '''
case "$*" in
  "-u +%u") echo "${FAKE_DOW:-3}" ;;
  "-u +%H%M") echo "${FAKE_HM:-1200}" ;;
  *) exec /bin/date "$@" ;;
esac
''')
    return bin_dir


def _doctor(tmp_path: Path, *fails: tuple[str, str]) -> Path:
    path = tmp_path / f"doctor-{abs(hash(fails))}.json"
    checks = [{"name": "python", "status": "OK", "detail": "3.11", "fix": "", "data": {}}]
    checks += [{"name": n, "status": "FAIL", "detail": d, "fix": "", "data": {}} for n, d in fails]
    path.write_text(json.dumps({"ok": not fails, "counts": {}, "checks": checks}))
    return path


def _workers(tmp_path: Path, *rows: tuple[str, str]) -> Path:
    path = tmp_path / f"workers-{abs(hash(rows))}.json"
    path.write_text(json.dumps({"workers": [
        {"worker_id": w, "kind": "research", "age_seconds": 7.0 if v == "fresh" else 900.0, "status": "ok",
         "last_error": None, "verdict": v} for w, v in rows], "leases": []}))
    return path


def _beat_env(tmp_path: Path, stubs: Path, **extra: str) -> dict[str, str]:
    return {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "FIBOKI_HOME": str(tmp_path / "fh"),
            "STUB_DOCTOR": str(_doctor(tmp_path)), "STUB_WORKERS": str(_workers(tmp_path, ("research@box", "fresh"))),
            "STUB_ENABLED": "", "STUB_FAILED": "", **extra}


def _beat(tmp_path: Path, stubs: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(SCRIPTS / "server-heartbeat.sh"), *args], env=_beat_env(tmp_path, stubs, **extra),
                inherit=False)


# ---------------------------------------------------------------------------- heartbeat


def test_a_healthy_box_sends_nothing_and_pings_the_dead_man_switch(tmp_path: Path, stubs: Path) -> None:
    proc = _beat(tmp_path, stubs, FIBOKI_HEALTHCHECK_URL="https://hc.example/ping/abc")
    assert proc.returncode == 0, proc.stderr
    calls = (tmp_path / "calls.log").read_text()
    assert 'url = "https://hc.example/ping/abc"' in calls and "/fail" not in calls
    assert "telegram" not in calls
    # The URL (a secret in practice) reaches curl on stdin, never on argv or in the journal.
    assert "hc.example" not in re.sub(r"STDIN.*", "", calls)
    assert "hc.example" not in proc.stdout + proc.stderr


def test_a_failing_doctor_row_pushes_to_telegram_without_the_token_on_argv(tmp_path: Path, stubs: Path) -> None:
    doctor = _doctor(tmp_path, ("data root", "/var/lib/fiboki/var/datastore missing"))
    proc = _beat(tmp_path, stubs, STUB_DOCTOR=str(doctor), FIBOKI_TELEGRAM_BOT_TOKEN="123456:SECRETTOKEN",
                 FIBOKI_TELEGRAM_CHAT_ID="-1001", FIBOKI_HEALTHCHECK_URL="https://hc.example/ping/abc")
    assert proc.returncode == 1
    calls = (tmp_path / "calls.log").read_text()
    argv_lines = [ln for ln in calls.splitlines() if ln.startswith("CURL")]
    argv_text = "\n".join(argv_lines) + "\n" + "\n".join(  # the message spans lines; keep the continuation
        ln for ln in calls.splitlines() if not ln.startswith(("CURL", "STDIN", "url", "SYSTEMCTL", "FIBOKI")))
    assert "chat_id=-1001" in argv_text and "data root" in argv_text
    assert "SECRETTOKEN" not in "\n".join(argv_lines), "the bot token must not be visible in ps"
    assert "url = \"https://api.telegram.org/botSECRETTOKEN" not in calls and "bot123456:SECRETTOKEN/sendMessage" in calls
    assert 'https://hc.example/ping/abc/fail' in calls
    assert "SECRETTOKEN" not in proc.stdout + proc.stderr
    assert "doctor data root" in proc.stderr or "data root" in proc.stderr


def test_ntfy_and_the_webhook_get_the_message_and_the_token_stays_off_argv(tmp_path: Path, stubs: Path) -> None:
    proc = _beat(tmp_path, stubs, STUB_DOCTOR=str(_doctor(tmp_path, ("disk free", "1.0 GiB"))),
                 FIBOKI_NTFY_URL="https://ntfy.example/topic-xyz", FIBOKI_NTFY_TOKEN="tk_NTFYSECRET",
                 FIBOKI_ALERT_WEBHOOK_URL="https://hooks.example/h/WEBHOOKSECRET")
    assert proc.returncode == 1
    calls = (tmp_path / "calls.log").read_text()
    argv = "\n".join(ln for ln in calls.splitlines() if ln.startswith("CURL"))
    assert "NTFYSECRET" not in argv and "WEBHOOKSECRET" not in argv and "topic-xyz" not in argv
    assert "header = \"Authorization: Bearer tk_NTFYSECRET\"" in calls and "Priority: high" in calls
    assert '"source": "fiboki-heartbeat"' in argv and '"severity": "problem"' in argv


def test_changes_push_at_once_repeats_wait_and_recovery_is_announced(tmp_path: Path, stubs: Path) -> None:
    tg = {"FIBOKI_TELEGRAM_BOT_TOKEN": "1:t", "FIBOKI_TELEGRAM_CHAT_ID": "9"}
    bad = str(_doctor(tmp_path, ("data root", "gone")))
    sends = lambda: sum(1 for ln in (tmp_path / "calls.log").read_text().splitlines()  # noqa: E731
                        if ln.startswith("CURL") and "sendMessage" not in ln and "chat_id" in ln)
    _beat(tmp_path, stubs, STUB_DOCTOR=bad, **tg)
    assert sends() == 1
    _beat(tmp_path, stubs, STUB_DOCTOR=bad, **tg)
    assert sends() == 1, "the same problem is not repeated every five minutes"
    worse = tmp_path / "worse.json"
    worse.write_text(json.dumps({"checks": [{"name": "data root", "status": "FAIL", "detail": "gone"},
                                            {"name": "disk free", "status": "FAIL", "detail": "full"}]}))
    _beat(tmp_path, stubs, STUB_DOCTOR=str(worse), **tg)
    assert sends() == 2, "a changed problem set pushes immediately"
    _beat(tmp_path, stubs, STUB_DOCTOR=str(worse), FIBOKI_HEARTBEAT_REPEAT_MINUTES="0", **tg)
    assert sends() == 3, "repeat interval elapsed"
    ok = _beat(tmp_path, stubs, **tg)
    assert ok.returncode == 0 and sends() == 4
    assert "all clear again" in (tmp_path / "calls.log").read_text()
    _beat(tmp_path, stubs, **tg)
    assert sends() == 4, "a healthy box stays quiet"


def test_stale_workers_dead_units_quotes_and_missing_backups_are_problems(tmp_path: Path, stubs: Path) -> None:
    workers = _workers(tmp_path, ("research@box", "stale"), ("paper-forward@box", "down"), ("x@box", "fresh"))
    proc = _beat(tmp_path, stubs, "--print", STUB_WORKERS=str(workers),
                 STUB_ENABLED="fiboki-api.service fiboki-paper.service fiboki-quotes.service",
                 STUB_FAILED="fiboki-paper.service", STUB_QUOTES_RC="1")
    assert proc.returncode == 1
    out = proc.stdout
    assert "worker research@box: stale" in out and "worker paper-forward@box: down" in out
    assert "x@box" not in out
    assert "unit fiboki-paper: failed" in out and "fiboki-api" not in out
    assert "quotes: recorder is not writing" in out
    calls = (tmp_path / "calls.log").read_text()
    assert "sendMessage" not in calls and "STDIN" not in calls, "--print sends and records nothing"
    assert not (tmp_path / "fh" / "heartbeat.state").exists()


def test_quote_freshness_is_not_checked_while_the_fx_market_is_closed(tmp_path: Path, stubs: Path) -> None:
    common = {"STUB_ENABLED": "fiboki-quotes.service", "STUB_QUOTES_RC": "1"}
    assert _beat(tmp_path, stubs, "--print", FAKE_DOW="6", FAKE_HM="1200", **common).returncode == 0   # Saturday
    assert _beat(tmp_path, stubs, "--print", FAKE_DOW="5", FAKE_HM="2300", **common).returncode == 0   # Friday after close
    assert _beat(tmp_path, stubs, "--print", FAKE_DOW="7", FAKE_HM="2200", **common).returncode == 0   # Sunday before open
    assert _beat(tmp_path, stubs, "--print", FAKE_DOW="7", FAKE_HM="2300", **common).returncode == 1   # open again
    assert _beat(tmp_path, stubs, "--print", FAKE_DOW="3", FAKE_HM="1200", **common).returncode == 1


def test_ignore_list_and_test_mode(tmp_path: Path, stubs: Path) -> None:
    bad = str(_doctor(tmp_path, ("calendar coverage", "ends in the past")))
    assert _beat(tmp_path, stubs, "--print", STUB_DOCTOR=bad).returncode == 1
    assert _beat(tmp_path, stubs, "--print", STUB_DOCTOR=bad,
                 FIBOKI_HEARTBEAT_IGNORE="calendar coverage").returncode == 0
    none = _beat(tmp_path, stubs, "--test")
    assert none.returncode == 1 and "no push channel configured" in none.stderr
    sent = _beat(tmp_path, stubs, "--test", FIBOKI_NTFY_URL="https://ntfy.example/t")
    assert sent.returncode == 0 and "sent a test through: ntfy" in sent.stdout


def test_a_failed_send_is_logged_by_channel_name_only(tmp_path: Path, stubs: Path) -> None:
    proc = _beat(tmp_path, stubs, STUB_DOCTOR=str(_doctor(tmp_path, ("data root", "gone"))),
                 FIBOKI_TELEGRAM_BOT_TOKEN="1:SECRETTOKEN", FIBOKI_TELEGRAM_CHAT_ID="9", STUB_HTTP="401")
    assert "telegram send failed (HTTP 401)" in proc.stderr and "SECRETTOKEN" not in proc.stderr + proc.stdout


# ------------------------------------------------------------------------------- backup


@pytest.fixture
def backup_env(tmp_path: Path, stubs: Path) -> dict[str, str]:
    var = tmp_path / "var"
    (var / "agents").mkdir(parents=True)
    (var / "agents" / "audit.jsonl").write_text('{"seq": 1}\n')
    (var / "datastore").mkdir()
    (var / "datastore" / ".fiboki-data-root").write_text("")
    (var / "datastore" / "bars.parquet").write_bytes(b"x" * 64)
    home = tmp_path / "fh"
    home.mkdir()
    (home / "env").write_text("FIBOKI_SESSION_SECRET=do-not-archive\n")
    _stub(stubs, "rclone", f'printf "RCLONE %s\\n" "$*" >> {tmp_path}/calls.log\nexit "${{STUB_RCLONE_RC:-0}}"\n')
    return {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "FIBOKI_STATE_DIR": str(var),
            "FIBOKI_HOME": str(home), "FIBOKI_BACKUP_DIR": str(tmp_path / "backups")}


def _server_backup(env: dict[str, str], *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return _run(["bash", str(SCRIPTS / "server-backup.sh"), *args], env={**env, **extra}, inherit=False)


def test_backup_without_an_offbox_target_says_so_every_night(backup_env: dict[str, str], tmp_path: Path) -> None:
    proc = _server_backup(backup_env, FIBOKI_BACKUP_DATASTORE="never")
    assert proc.returncode == 0, proc.stderr
    assert "no off-box target configured" in proc.stderr and "share a disk" in proc.stderr
    (archive,) = (tmp_path / "backups" / "daily").glob("fiboki-backup-*.tar.gz")
    with tarfile.open(archive) as tar:
        names = [m.name for m in tar.getmembers()]
    assert not any("/env" in n.replace("var/", "") and n.endswith("/env") for n in names), "the env file is never archived"
    assert not any("datastore" in n for n in names)
    assert (tmp_path / "fh" / "backup.last").read_text().split()[1] == "offbox=none"


def test_backup_prunes_to_the_retention_and_weekly_includes_the_datastore(
    backup_env: dict[str, str], tmp_path: Path
) -> None:
    for _ in range(3):
        proc = _server_backup(backup_env, FIBOKI_BACKUP_KEEP="2", FIBOKI_BACKUP_DATASTORE="always",
                              FIBOKI_BACKUP_KEEP_WEEKLY="1")
        assert proc.returncode == 0, proc.stderr
        time.sleep(1.1)   # archive names have one-second resolution
    daily = sorted((tmp_path / "backups" / "daily").glob("fiboki-backup-*.tar.gz"))
    weekly = sorted((tmp_path / "backups" / "weekly").glob("fiboki-backup-*.tar.gz"))
    assert len(daily) == 2 and len(weekly) == 1
    assert len(list((tmp_path / "backups" / "daily").glob("*.sha256"))) == 2, "checksums are pruned with their archive"
    with tarfile.open(weekly[0]) as tar:
        assert any(m.name.endswith("var/datastore/bars.parquet") for m in tar.getmembers())
    assert "pruned" in proc.stdout


def test_backup_copies_off_box_with_rclone_and_fails_loudly_when_that_fails(
    backup_env: dict[str, str], tmp_path: Path
) -> None:
    ok = _server_backup(backup_env, FIBOKI_BACKUP_DATASTORE="never", FIBOKI_BACKUP_RCLONE_REMOTE="b2:bucket/server1")
    assert ok.returncode == 0, ok.stderr
    calls = (tmp_path / "calls.log").read_text()
    assert "RCLONE copyto" in calls and "b2:bucket/server1/daily/fiboki-backup-" in calls and ".sha256" in calls
    assert "no off-box target" not in ok.stderr
    assert "offbox=rclone" in (tmp_path / "fh" / "backup.last").read_text()
    time.sleep(1.1)
    (tmp_path / "fh" / "backup.last").unlink()
    bad = _server_backup(backup_env, FIBOKI_BACKUP_DATASTORE="never", FIBOKI_BACKUP_RCLONE_REMOTE="b2:bucket/server1",
                         STUB_RCLONE_RC="1")
    assert bad.returncode == 1 and "OFF-BOX copy FAILED" in bad.stderr
    assert not (tmp_path / "fh" / "backup.last").exists(), "a failed off-box copy is not recorded as a success"
    time.sleep(1.1)
    nore = _server_backup(backup_env, FIBOKI_BACKUP_DATASTORE="never", FIBOKI_BACKUP_RESTIC_REPOSITORY="sftp:u@h:/r")
    assert nore.returncode == 1 and "restic is not installed" in nore.stderr


def test_backup_print_and_bad_settings(backup_env: dict[str, str]) -> None:
    shown = _server_backup(backup_env, "--print", FIBOKI_BACKUP_DATASTORE="weekly")
    assert shown.returncode == 0 and "rclone      : not configured" in shown.stdout
    assert _server_backup(backup_env, FIBOKI_BACKUP_KEEP="x").returncode == 2
    assert _server_backup(backup_env, FIBOKI_BACKUP_DATASTORE="sometimes").returncode == 2


# --------------------------------------------------------------- migration: the paper gate


@pytest.fixture(scope="module")
def preflight():
    spec = importlib.util.spec_from_file_location("_migrate_preflight", SCRIPTS / "migrate_preflight.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session(state: Path, wiring: str = "paper_forward_v1", *, snapshot: str = "none", open_n: int = 1,
             summary: bool = True) -> Path:
    """A paper ledger directory as the runtime leaves it."""
    from fiboki.core.durable import write_record_atomic

    ledger = state / "paper_forward" / wiring
    ledger.mkdir(parents=True)
    (ledger / "trades.jsonl").write_text('{"t": 1}\n')
    (ledger / "intents.jsonl").write_text('{"i": 1}\n')
    if summary:
        session = state / "paper" / f"forward-{wiring}"
        session.mkdir(parents=True)
        (session / "summary.json").write_text(json.dumps({"open_positions": open_n}))
    if snapshot in ("ok", "stale"):
        wm = {"trades_bytes": (ledger / "trades.jsonl").stat().st_size,
              "intents_bytes": (ledger / "intents.jsonl").stat().st_size + (5 if snapshot == "stale" else 0)}
        body = {"state": {"manager": {"book": {"open": [{"id": f"p{i}"} for i in range(open_n)]}}}, "watermark": wm}
        write_record_atomic(ledger / "state_snapshot.json", json.dumps(body, sort_keys=True))
    elif snapshot == "corrupt":
        (ledger / "state_snapshot.json").write_text("not a framed record\n")
    return ledger


def _assess(preflight, state: Path, running: set[str], **kw):
    return preflight.assess(state, state / "paper", running, require_fresh=kw.get("fresh", False),
                            accept_abandoned=kw.get("accept", False))


def test_a_running_session_with_an_open_position_and_no_snapshot_is_refused(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="none", open_n=1)
    (finding,) = _assess(preflight, tmp_path, {"paper"})
    assert finding.verdict == "refuse" and finding.open_positions == 1 and finding.snapshot == "absent"
    assert "RUNNING Mac process" in finding.message and "positions_abandoned_at_restart" in finding.message
    assert "unrealised P&L" in finding.message and "--accept-abandoned-positions" in finding.message


def test_an_unknown_position_count_is_treated_as_possibly_open(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="none", summary=False)
    (finding,) = _assess(preflight, tmp_path, {"paper"})
    assert finding.verdict == "refuse" and finding.open_source == "unknown" and "unknown number of" in finding.message


def test_a_corrupt_snapshot_does_not_count_as_a_snapshot(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="corrupt", open_n=2)
    (finding,) = _assess(preflight, tmp_path, {"paper"})
    assert finding.verdict == "refuse" and finding.snapshot == "corrupt"


def test_a_verifying_snapshot_lets_the_positions_move(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="ok", open_n=2)
    (finding,) = _assess(preflight, tmp_path, {"paper"})
    assert finding.verdict == "ok" and finding.open_positions == 2 and finding.open_source == "snapshot"
    assert "restores them on first start" in finding.message


def test_a_flat_session_moves_without_a_snapshot(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="none", open_n=0)
    (finding,) = _assess(preflight, tmp_path, {"paper"})
    assert finding.verdict == "ok" and "flat" in finding.message


def test_after_the_stop_a_ledger_that_outgrew_the_snapshot_is_refused_as_stale(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="stale", open_n=1)
    assert _assess(preflight, tmp_path, {"paper"}, fresh=False)[0].verdict == "ok"
    (finding,) = _assess(preflight, tmp_path, {"paper"}, fresh=True)
    assert finding.verdict == "refuse" and "stale" in finding.message


def test_accepting_the_loss_downgrades_the_refusal_and_a_stopped_session_only_warns(preflight, tmp_path: Path) -> None:
    _session(tmp_path, snapshot="none", open_n=1)
    assert _assess(preflight, tmp_path, {"paper"}, accept=True)[0].verdict == "warn"
    (finding,) = _assess(preflight, tmp_path, set())
    assert finding.verdict == "warn" and "Any restart" in finding.message


def test_crypto_and_fx_are_judged_separately(preflight, tmp_path: Path) -> None:
    _session(tmp_path, "paper_forward_v1", snapshot="none", open_n=1)
    _session(tmp_path, "paper_forward_crypto_v1", snapshot="ok", open_n=1)
    verdicts = {f.service: f.verdict for f in _assess(preflight, tmp_path, {"paper", "paper-crypto"})}
    assert verdicts == {"paper": "refuse", "paper-crypto": "ok"}
    # Only the FX session runs: the crypto one is not a refusal either way.
    verdicts = {f.service: f.verdict for f in _assess(preflight, tmp_path, {"paper"})}
    assert verdicts["paper-crypto"] == "ok"


def _migrate(tmp_path: Path, stubs: Path, *args: str, running: str = "paper", **extra: str) -> subprocess.CompletedProcess[str]:
    _stub(stubs, "launchctl", f'''
case "$*" in
  "print gui/"*"/uk.fiboki."*)
    svc="${{2##*uk.fiboki.}}"
    case ",{running}," in *",$svc,"*) echo "state = running"; exit 0;; esac
    exit 113 ;;
esac
exit 0
''')
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "FIBOKI_STATE_DIR": str(tmp_path / "var"),
           "FIBOKI_HOME": str(tmp_path / "fh"), **extra}
    return _run(["bash", str(SCRIPTS / "server-migrate.sh"), "--host", "joe@203.0.113.9", *args], env=env, inherit=False)


def test_the_script_refuses_before_doing_anything_and_explains(tmp_path: Path, stubs: Path) -> None:
    _session(tmp_path / "var", snapshot="none", open_n=1)
    proc = _migrate(tmp_path, stubs, "--dry-run", "prepare")
    assert proc.returncode == 1
    assert "REFUSED: a paper session cannot be moved safely" in proc.stderr
    assert "RUNNING Mac process" in proc.stdout and "positions_abandoned_at_restart" in proc.stdout
    assert "DRY" not in proc.stdout and "backup.sh" not in proc.stdout, "nothing was even planned"
    accepted = _migrate(tmp_path, stubs, "--dry-run", "--accept-abandoned-positions", "preflight")
    assert accepted.returncode == 0 and "WARN" in accepted.stdout
    calm = _migrate(tmp_path, stubs, "--dry-run", "preflight", running="api,worker")
    assert calm.returncode == 0, "no paper process running: nothing to protect"


def test_a_snapshot_lets_the_move_proceed_and_the_dry_run_orders_the_steps_safely(tmp_path: Path, stubs: Path) -> None:
    _session(tmp_path / "var", snapshot="ok", open_n=1)
    proc = _migrate(tmp_path, stubs, "--dry-run", "all", "--yes", running="api,worker,web,news,paper")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = proc.stdout
    order = [out.index(m) for m in (
        "== Prepare", "backup.sh", "rsync -az --partial --progress", "restore.sh /var/backups/fiboki/incoming/",
        "systemctl start fiboki-api.service fiboki-web.service", "== Gate: the server must be healthy",
        "== Stop on the Mac", "== Gate again", "== Final sync", "== Start on the server")]
    assert order == sorted(order), "backup, copy, restore, prove healthy, THEN stop the Mac, final sync, start there"
    assert "launchd-install.sh --unload --services paper" in out
    assert out.index("--unload --services paper") < out.index("--unload --services worker") < out.index("--unload --services api"), \
        "the position-holding paper session stops first"
    start = out.split("== Start on the server", 1)[1]
    assert start.index("fiboki-paper.service") > start.index("fiboki-api.service")
    assert "--exclude /logs/" in out and "--exclude /datastore/" in out
    assert "env" not in re.findall(r"rsync[^\n]*", out)[0].split("/")[-1], "the secrets file is never transferred"
    assert "FIBOKI_SESSION_SECRET" not in out


def test_rollback_stops_the_server_and_restarts_the_mac(tmp_path: Path, stubs: Path) -> None:
    (tmp_path / "fh").mkdir()
    (tmp_path / "fh" / "server-migrate.last").write_text(
        "HOST=joe@203.0.113.9\nRPREFIX=/opt/fiboki\nRLIB=/var/lib/fiboki\nSERVICES=paper,worker,api\nAT=2026-10-20T10:00:00Z\n")
    proc = _migrate(tmp_path, stubs, "--dry-run", "rollback", running="")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "using the services recorded at cutover: paper,worker,api" in out
    assert "systemctl stop fiboki-paper.service fiboki-worker.service fiboki-api.service" in out
    assert "launchd-install.sh --services paper,worker,api --load" in out
    assert "re-run with --pull-back" in out
    pulled = _migrate(tmp_path, stubs, "--dry-run", "--pull-back", "rollback", running="")
    assert "--backup --backup-dir=" in pulled.stdout and ".pre-pullback-" in pulled.stdout


def test_migrate_cli_misuse(tmp_path: Path, stubs: Path) -> None:
    assert _run(["bash", str(SCRIPTS / "server-migrate.sh"), "prepare"]).returncode == 2
    assert _run(["bash", str(SCRIPTS / "server-migrate.sh"), "--host", "a b", "prepare"]).returncode == 2
    assert _run(["bash", str(SCRIPTS / "server-migrate.sh"), "--host", "h"]).returncode == 2
    assert _migrate(tmp_path, stubs, "--services", "nope", "preflight").returncode == 2


# ---------------------------------------------------------------------------- docs


def test_the_runbook_and_pointers_exist_and_name_the_real_commands() -> None:
    server = (REPO / "docs" / "v2" / "SERVER.md").read_text()
    for needle in ("server-install.sh", "server-migrate.sh", "tailscale serve", "/etc/fiboki/env", "server-heartbeat.sh",
                   "healthchecks.io", "rollback", "netcup", "fiboki-ctl", "--no-start", "USER_ACTIONS", "no public port"):
        assert needle in server, needle
    assert "SERVER.md" in (REPO / "docs" / "v2" / "DEPLOYMENT.md").read_text()
    assert "SERVER.md" in (REPO / "docs" / "v2" / "OPERATIONS.md").read_text()
    assert "### M4. Cloud server" in (REPO / "USER_ACTIONS.md").read_text()
    assert "SERVER.md" in (REPO / "deploy" / "README.md").read_text()
    assert "\u2014" not in server, "no em dashes (house style)"
