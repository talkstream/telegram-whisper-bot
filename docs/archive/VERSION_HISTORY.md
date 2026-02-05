# Telegram Whisper Bot - Version History

## Current Version: v3.4.0 (February 6, 2026)

---

## v3.x - Alibaba Cloud Era

| Version | Date | Changes |
|---------|------|---------|
| v3.4.0 | 2026-02-06 | Evolving progress messages, typing indicators, LLM threshold 100 chars, DB 6→4 calls |
| v3.3.0 | 2026-02-04 | Shared services refactor (~2500 lines dedup), 'PUT'→'put' fix, pre-deploy actions |
| v3.2.0 | 2026-02-04 | Payment handler fix, /admin, /user pagination, low balance alerts, scheduled reports |
| v3.1.1 | 2026-02-04 | Documentation sync |
| v3.1.0 | 2026-02-04 | Admin improvements, user search, data export |
| v3.0.1 | 2026-02-04 | qwen-turbo LLM, formatting threshold 150 words |
| v3.0.0 | 2026-02-04 | Complete Alibaba migration: FC 3.0, Tablestore, MNS, Qwen3-ASR-Flash REST |

## v2.x - Multi-Backend ASR

| Version | Date | Changes |
|---------|------|---------|
| v2.1.0 | 2026-02-04 | Multi-backend ASR (openai, faster-whisper, qwen-asr), FFmpeg multithreading |
| v2.0.0 | 2026-02-04 | Cloud Logging exclusion filter, GPU Whisper support |

## v1.9.x - Cost Optimization

| Version | Date | Changes |
|---------|------|---------|
| v1.9.0 | 2026-02-04 | Smart Cold Start UX, Cloud Logging optimization, warmup 10 min |

## v1.8.x - Architecture Refactoring

| Version | Date | Changes |
|---------|------|---------|
| v1.8.2 | 2025-07-05 | Fixed fractional minute display |
| v1.8.1 | 2025-07-04 | SDK migration vertexai → google-genai |
| v1.8.0 | 2025-07-04 | Major refactoring: main.py 1369→356 lines (-74%) |

## v1.7.x - Video Support

| Version | Date | Changes |
|---------|------|---------|
| v1.7.5 | 2025-07-04 | /yo command, unified /code toggle |
| v1.7.0-v1.7.4 | 2025-06-26-27 | Video transcription, Gen AI SDK migration, bug fixes |

## v1.0.x-v1.6.x - Foundation

| Version | Date | Changes |
|---------|------|---------|
| v1.6.x | 2025-06-25 | Automated reports, CSV export |
| v1.5.x | 2025-06-25 | Export functionality |
| v1.4.x | 2025-06-25 | Performance monitoring, /metrics |
| v1.3.x | 2025-06-25 | Trial system, inline keyboards |
| v1.2.x | 2025-06-25 | Tariff system (300% markup) |
| v1.1.x | 2025-06-25 | Service-oriented architecture (-40% code) |
| v1.0.x | 2025-06-24 | Initial stable release |

---

## Key Milestones

| Date | Milestone |
|------|-----------|
| 2025-06-24 | v1.0.0 Initial release |
| 2025-06-25 | 7 releases (v1.1-v1.6), service architecture |
| 2025-06-26-27 | Video support, Gen AI SDK |
| 2025-07-04-05 | Architecture overhaul (-74% main.py) |
| 2026-02-04 | Alibaba migration (-68% cost: $25→$8/mo) |
| 2026-02-06 | UX overhaul: evolving progress, DB optimization |

---

**Repository:** https://github.com/talkstream/telegram-whisper-bot (private)
