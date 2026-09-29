"""``fiboki.core.env_file`` parses ``~/.fiboki/env`` exactly as
``scripts/fiboki-service.sh`` does: no expansion, quotes stripped, malformed
keys reported. The shell reader is exercised in test_desktop_scripts; this
file pins the Python side and that the two agree on the same input."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from fiboki.core.env_file import env_file_path, parse_env_file, read_env_file

REPO = Path(__file__).resolve().parents[2]
SAMPLE = (
    "# comment\n"
    "\n"
    "FIBOKI_OPERATORS='joe:admin:scrypt$16384$8$1$abc$def'\n"
    'FIBOKI_SESSION_SECRET="s3cr$et=with=equals"\n'
    "PLAIN=$HOME/unexpanded ~/tilde\n"
    "SPACES= padded value \n"
    "EMPTY=\n"
    "ONEQUOTE='unterminated\n"
    "bad-key=ignored\n"
    "lower=ignored\n"
    "noequals\n"
)
EXPECTED = {
    "FIBOKI_OPERATORS": "joe:admin:scrypt$16384$8$1$abc$def",
    "FIBOKI_SESSION_SECRET": "s3cr$et=with=equals",
    "PLAIN": "$HOME/unexpanded ~/tilde",
    "SPACES": " padded value ",
    "EMPTY": "",
    "ONEQUOTE": "'unterminated",
}


def test_parse_never_expands_and_reports_malformed_keys() -> None:
    parsed = parse_env_file(SAMPLE)
    assert parsed.values == EXPECTED
    assert parsed.malformed == ("bad-key", "lower", "noequals")
    assert parsed.exists


def test_read_missing_file_is_empty_not_an_error(tmp_path: Path) -> None:
    missing = read_env_file(tmp_path / "env")
    assert not missing.exists and missing.values == {} and missing.path == tmp_path / "env"
    (tmp_path / "env").write_text("A=1\n")
    assert read_env_file(tmp_path / "env").values == {"A": "1"}
    assert not read_env_file(tmp_path / "env" / "nested").exists


def test_env_file_path_follows_fiboki_home_and_the_mapping_home() -> None:
    assert env_file_path({"HOME": "/Users/x"}) == Path("/Users/x/.fiboki/env")
    assert env_file_path({"HOME": "/Users/x", "FIBOKI_HOME": "~/alt"}) == Path("/Users/x/alt/env")
    assert env_file_path({"FIBOKI_HOME": "/srv/fiboki"}) == Path("/srv/fiboki/env")


@pytest.mark.skipif(os.name != "posix", reason="runs the bash reader")
def test_python_and_shell_readers_agree(tmp_path: Path) -> None:
    text = (REPO / "scripts" / "fiboki-service.sh").read_text()
    block = text[text.index('ENV_FILE="${FIBOKI_HOME') : text.index("# ---- paper only")]
    (tmp_path / "env").write_text(SAMPLE)
    keys = list(EXPECTED)
    script = (
        "set -euo pipefail\n"
        + block
        + "\n"
        + "".join(f'printf "%s\\036" "${{{k}-<unset>}}"\n' for k in keys)
    )
    run = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"HOME": str(tmp_path), "FIBOKI_HOME": str(tmp_path), "PATH": os.environ["PATH"]},
    )
    assert run.returncode == 0, run.stderr
    shell = dict(zip(keys, run.stdout.split("\x1e")[: len(keys)], strict=True))
    assert shell == EXPECTED
    for key in parse_env_file(SAMPLE).malformed:
        assert key in run.stderr
