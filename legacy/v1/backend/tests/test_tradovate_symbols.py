"""Tests for Tradovate symbol/contract resolution."""

from __future__ import annotations

from fibokei.execution.broker_symbols import (
    TradovateContract,
    TradovateContractResolver,
    UnsupportedSymbol,
    _ContractMapping,
    _parse_env_symbol_map,
)


class TestParseEnvSymbolMap:
    def test_empty(self):
        assert _parse_env_symbol_map("") == {}

    def test_single_pair(self):
        m = _parse_env_symbol_map("US500:ES")
        assert "US500" in m
        assert m["US500"].product_code == "ES"

    def test_multiple_pairs_with_whitespace(self):
        m = _parse_env_symbol_map(" US500:ES , US100:NQ ,XAUUSD:MGC")
        assert set(m.keys()) == {"US500", "US100", "XAUUSD"}
        assert m["XAUUSD"].product_code == "MGC"

    def test_invalid_entries_skipped(self):
        m = _parse_env_symbol_map("US500:ES, BAD, :NQ, US100:")
        # BAD has no colon, :NQ has empty key, US100: has empty value → all dropped
        assert list(m.keys()) == ["US500"]


class TestResolver:
    def test_unmapped_symbol_returns_unsupported(self, monkeypatch):
        monkeypatch.delenv("FIBOKEI_TRADOVATE_SYMBOL_MAP", raising=False)
        monkeypatch.delenv("FIBOKEI_TRADOVATE_FRONT_MONTH", raising=False)
        r = TradovateContractResolver()
        result = r.resolve("EURUSD")
        assert isinstance(result, UnsupportedSymbol)
        assert result.code == "UNSUPPORTED_INSTRUMENT_TRADOVATE"

    def test_missing_front_month(self):
        r = TradovateContractResolver(
            symbol_map={"US500": _ContractMapping(product_code="ES")}
        )
        # No front-month configured
        result = r.resolve("US500")
        assert isinstance(result, UnsupportedSymbol)
        assert result.code == "MISSING_FRONT_MONTH"

    def test_resolves_with_front_month(self):
        r = TradovateContractResolver(
            front_month_suffix="M6",
            symbol_map={"US500": _ContractMapping(product_code="ES")},
        )
        result = r.resolve("US500")
        assert isinstance(result, TradovateContract)
        assert result.contract_symbol == "ESM6"
        assert result.product_code == "ES"

    def test_explicit_contract_overrides(self):
        r = TradovateContractResolver(
            symbol_map={
                "X": _ContractMapping(
                    product_code="ES",
                    explicit_contract_symbol="ES_FRONT_QUARTERLY",
                )
            },
        )
        # Even without front-month suffix, explicit override is used
        result = r.resolve("X")
        assert isinstance(result, TradovateContract)
        assert result.contract_symbol == "ES_FRONT_QUARTERLY"

    def test_supported_symbols(self):
        r = TradovateContractResolver(
            symbol_map={
                "US500": _ContractMapping(product_code="ES"),
                "US100": _ContractMapping(product_code="NQ"),
            },
        )
        assert r.supported_symbols() == ["US100", "US500"]

    def test_empty_input(self):
        r = TradovateContractResolver()
        result = r.resolve("")
        assert isinstance(result, UnsupportedSymbol)
        assert result.code == "EMPTY_SYMBOL"
