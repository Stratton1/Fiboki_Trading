# Operator note, 19 October 2026

This records decisions taken with the laptop work after the E-4 null. It does not change a gate, a cost, or an execution mode.

## Local model

`FIBOKI_AGENT_PROVIDER` is `local`. `FIBOKI_AGENT_LOCAL_MODEL` is `qwen3:4b`. `FIBOKI_AGENT_CYCLES` is `true`. `FIBOKI_AGENT_CYCLE_TARGET` is `donchian_breakout_atr:XAUUSD:H4`. The local URL is the Ollama default on loopback. Those keys were read from `~/.fiboki/env`. Tokens and password hashes were not printed.

P7 recorded a smoke of Ollama `qwen3:4b` (digest prefix `sha256:359d7dd4`). On this pass `ollama list` showed no models. One `smoke_test_provider` call against `qwen3:4b` returned not ok: Ollama answered HTTP 404, `model 'qwen3:4b' not found`, on both the fingerprint and the generate step inside that single smoke. `var/agents/smoke.json` records that. `run_evals` on `var/agents/audit.jsonl` wrote `var/agents/eval_report.json`: 1,621 records, hash chain intact, verdict `NOT_EVALUABLE`. The Agent Desk states that one smoke and one eval do not finish the 20-run acceptance protocol. The scan interval and the 02:15 UTC cycle were not changed. No model was downloaded. The 27B pin stays the 32 GB desktop tier in `docs/v2/SERVER.md`.

## Hugging Face and fine-tuning

The pinned GGUF list is already Unsloth Qwen3.5-4B, Qwen3.5-9B, and Qwen3.6-27B, each with a commit and a sha256 in `docs/v2/SERVER.md`. Finance-labelled and "forex" instruction tunes on Hugging Face are another class of note-writer. None has been through this ladder. Adopting one later means a reviewed pin (repo, commit, filename, sha256, context length) and a digest on every audit row, still at tier T1, still unable to emit an `Order`. None was downloaded.

A LoRA or full fine-tune on Fiboki journals would train on the search that already failed. A slice that includes holdout bars or holdout reports spends the holdout. `docs/v2/research/DATA_SOURCES_2026-10.md` keeps IMF text out of fine-tuning. Coinbase's market-data terms restrict using their data to improve a model. No training job was started and no weights were changed.

## Reading corpus

`FibokiPaths.corpus_dir` is `<FIBOKI_STATE_DIR>/corpus`, from `core/paths.resolve_paths`. `fiboki.research.corpus.load_manifest` reads JSONL. A line needs `title`, `source_url`, `retrieved_at`, `licence`, and `sha256`. Allowed licences: `public-domain`, `cc0`, `cc-by`, `cc-by-sa`, and `publisher-research` (the last also needs `terms_url`). A missing licence, an unknown licence, or a missing file raises. Unknown terms are a refusal.

Refused, and not given a licence id: scanned paid books, mirrors of in-copyright books, ForexFactory or Investing.com extracts, IMF bulk text, Coinbase text destined for training, and holdout text. The loader does not download, does not update weights, and does not write the experiment ledger. The workstation has no PDF upload. A scheduled librarian pass is not enabled on this 8 GB machine.

## Research memory

`GET /api/intelligence/research-memory` returns `available: false` when no experiment database is configured, and HTTP 501 `research_memory_not_wired` when one is. The research-memory page says a folder of readings does not connect it. The corpus manifest is not stuffed into the experiment ledger as trials. Wiring recall over HTTP remains a later change.

## Hardware

This Mac's memory is soldered. An AliExpress DIMM, USB "RAM" module, or similar part does not add memory the chip can use. A separate GPU board or mini PC would be a second computer, a second place for `~/.fiboki/env`, and a risk of a second paper writer. No parts were bought. The desktop already chosen (32 GB, 2 TB, one checkout, laptop paper unloaded first, `scripts/llama-server.sh` selecting Qwen3.6-27B Q4_K_M) stays the later move.

## Paper

The FX restart, if the snapshot was still flat at the moment of the restart, is recorded in the paper journal for that start (`calendar_version` on `session_start`, and the banner line `calendar:`). Crypto was left running. No spread table was built from the quote recorder.
