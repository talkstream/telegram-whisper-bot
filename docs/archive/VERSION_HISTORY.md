# Version History

## Current: v4.0.0 (2026-02-09)

## v4.0 + v3.x — Alibaba Cloud

| Version | Date | Changes |
|---------|------|---------|
| v4.0.0 | 2026-02-09 | **Major**: 3 diarization backends (DashScope/AssemblyAI/Gemini), word-level timestamps, `/speakers` `/debug`, bulletproof async pipeline, direct HTTP fallback. 166 tests. |
| v3.6.0 | 2026-02-07 | Diarization (Fun-ASR), `/output` `/dialogue` `/mute`, `_build_format_prompt()` DRY, proper noun/sibilant rules, SLS logging, TelegramErrorHandler, `send_as_file()`. 86 tests. |
| v3.5.0 | 2026-02-07 | ASR chunking (>150s auto-split), document handler, adaptive compression, user-friendly errors. 55 tests. |
| v3.4.0 | 2026-02-06 | Evolving progress messages, typing indicators, LLM threshold 100 chars, DB 6→4 calls |
| v3.3.0 | 2026-02-04 | Shared services refactor (~2500 lines dedup), `'PUT'`→`'put'` fix, pre-deploy actions |
| v3.2.0 | 2026-02-04 | Payment fix, `/admin`, `/user` pagination, low balance alerts, reports |
| v3.1.x | 2026-02-04 | Admin improvements, user search, data export |
| v3.0.x | 2026-02-04 | Alibaba migration: FC 3.0, Tablestore, MNS, Qwen3-ASR-Flash REST, qwen-turbo LLM |

## v2.x — Multi-Backend ASR

| Version | Date | Changes |
|---------|------|---------|
| v2.1.0 | 2026-02-04 | Multi-backend ASR (openai, faster-whisper, qwen-asr), FFmpeg multithreading |
| v2.0.0 | 2026-02-04 | Cloud Logging filter, GPU Whisper support |

## v1.x — GCP Era

| Version | Date | Changes |
|---------|------|---------|
| v1.9.0 | 2026-02-04 | Smart Cold Start UX, Cloud Logging optimization |
| v1.8.x | 2025-07 | Architecture refactor: main.py -74%, SDK migration to google-genai |
| v1.7.x | 2025-06-26 | Video support, `/yo`, `/code` |
| v1.0–v1.6 | 2025-06-24–25 | Foundation: service architecture, tariffs, trials, monitoring, exports, reports |

## Milestones

| Date | Event |
|------|-------|
| 2025-06-24 | v1.0.0 initial release |
| 2025-07-04 | Architecture overhaul (-74% main.py) |
| 2026-02-04 | Alibaba migration (-68% cost: $25→$8/mo) |
| 2026-02-07 | v3.6.0 diarization, 86 tests |
| 2026-02-09 | v4.0.0 multi-backend diarization, 166 tests |

---

*v4.0.0*
