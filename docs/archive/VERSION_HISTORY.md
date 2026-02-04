# Telegram Whisper Bot - Version History

## Current Version: v3.0.1 (February 4, 2026)
Full Alibaba Cloud migration with optimizations.

---

## Major Releases

### v3.x - Alibaba Cloud Era

| Version | Date | Changes |
|---------|------|---------|
| v3.0.1 | 2026-02-04 | Speed/cost optimizations: qwen-turbo LLM, formatting threshold 150 words |
| v3.0.0 | 2026-02-04 | Complete Alibaba migration: FC 3.0, Tablestore, MNS, Qwen3-ASR-Flash REST |

### v2.x - Multi-Backend ASR

| Version | Date | Changes |
|---------|------|---------|
| v2.1.0 | 2026-02-04 | Multi-backend ASR (openai, faster-whisper, qwen-asr), FFmpeg multithreading |
| v2.0.0 | 2026-02-04 | Cloud Logging exclusion filter, GPU Whisper support (faster-whisper) |

### v1.9.x - Cost Optimization

| Version | Date | Changes |
|---------|------|---------|
| v1.9.0 | 2026-02-04 | Smart Cold Start UX, Cloud Logging optimization, warmup interval 10 min |

### v1.8.x - Architecture Refactoring

| Version | Date | Changes |
|---------|------|---------|
| v1.8.2 | 2025-07-05 | Fixed fractional minute display (math.ceil for all) |
| v1.8.1 | 2025-07-04 | Completed SDK migration from vertexai to google-genai |
| v1.8.0 | 2025-07-04 | Major refactoring: main.py 1369->356 lines (74% reduction) |

### v1.7.x - Video Support & UI

| Version | Date | Changes |
|---------|------|---------|
| v1.7.5 | 2025-07-04 | New /yo command, unified /code toggle |
| v1.7.4 | 2025-06-27 | Improved error messages UI (removed alarming emoji) |
| v1.7.3 | 2025-06-26 | "Prodolzheniye sleduyet..." detection for speechless audio |
| v1.7.2 | 2025-06-26 | Fixed Gemini instruction leak to users on short transcripts |
| v1.7.1 | 2025-06-26 | Migrated to Google Gen AI SDK (deprecation fix) |
| v1.7.0 | 2025-06-26 | Video transcription support (MP4, AVI, MOV, MKV, WebM) |

### v1.6.x - Admin Reports

| Version | Date | Changes |
|---------|------|---------|
| v1.6.1 | 2025-06-25 | Fixed CSV export send_document parameter |
| v1.6.0 | 2025-06-25 | Automated daily/weekly reports via Cloud Scheduler |

### v1.5.x - Export

| Version | Date | Changes |
|---------|------|---------|
| v1.5.0 | 2025-06-25 | CSV export functionality (/export command) |

### v1.4.x - Monitoring

| Version | Date | Changes |
|---------|------|---------|
| v1.4.1 | 2025-06-25 | GitHub repository setup, documentation updates |
| v1.4.0 | 2025-06-25 | Performance monitoring (MetricsService), /metrics command |

### v1.3.x - Trial & Payments

| Version | Date | Changes |
|---------|------|---------|
| v1.3.1 | 2025-06-25 | Improved /cost command clarity |
| v1.3.0 | 2025-06-25 | Optimized trial requests, re-enabled inline keyboards |

### v1.2.x - Tariffs

| Version | Date | Changes |
|---------|------|---------|
| v1.2.0 | 2025-06-25 | New tariff system with progressive pricing (300%->200% markup) |

### v1.1.x - Service Architecture

| Version | Date | Changes |
|---------|------|---------|
| v1.1.0 | 2025-06-25 | Service-oriented architecture (40% code reduction) |

### v1.0.x - Initial Release

| Version | Date | Changes |
|---------|------|---------|
| v1.0.9 | 2025-06-25 | Major refactoring with service layer |
| v1.0.8 | 2025-06-25 | Improved UX and automatic cleanup |
| v1.0.7 | 2025-06-24 | Multiple critical fixes |
| v1.0.6 | 2025-06-24 | Added /help command |
| v1.0.5 | 2025-06-24 | Duration accuracy improvements |
| v1.0.4 | 2025-06-24 | Fixed /settings HTML parsing error |
| v1.0.3 | 2025-06-24 | Critical fixes: Flask integration |
| v1.0.2 | 2025-06-24 | Performance improvements, warmup optimization |
| v1.0.1 | 2025-06-24 | Codebase cleanup |
| v1.0.0 | 2025-06-24 | Initial stable release |

---

## Key Milestones

### June 25, 2025 - Productive Day
- 7 major releases (v1.1.0 -> v1.6.1)
- Complete service-oriented architecture
- Progressive tariff system
- Performance monitoring
- CSV export and automated reports
- GitHub repository setup

### June 26-27, 2025 - Video Support
- Video transcription (v1.7.0)
- Google Gen AI SDK migration
- Multiple bug fixes

### July 4-5, 2025 - Architecture Overhaul
- 74% code reduction in main.py
- Modular app/ structure
- Fractional minute fixes

### February 4, 2026 - Alibaba Migration
- Complete GCP -> Alibaba Cloud migration
- 68% cost reduction ($25/mo -> $8/mo)
- Qwen3-ASR-Flash for transcription
- Qwen-plus/turbo for formatting

---

## GitHub Repository
- **URL**: https://github.com/talkstream/telegram-whisper-bot.git
- **Visibility**: Private
- **Main Branch**: main
