# Audit Roadmap v4.3.0

*2026-02-19 — Expert panel deep audit before scaling*

## Context

Project reached MVP+ stage (v4.3.0): ASR, diarization, parallel processing, auto-trial, payments. Before scaling and fundraising, a comprehensive audit is needed to identify tech debt, risks, and growth points.

**Blast radius**: cross-system (billing, audio processing, result delivery, user data)

---

## Expert Panel Summary

| Expert | Rating | Key Finding |
|--------|--------|-------------|
| Business Owner | Production-ready, 2 critical risks | Balance deduction order allows free transcriptions on failure; no unit-economics metrics |
| System Architect | Solid arch, 4 functions >100 LOC | `handle_command` (243), `process_job` (284), `process_audio_sync` (208), `transcribe_with_diarization` (174) |
| Security Analyst | Secrets handled well, 4 issues | CVE-2024-35195 in requests 2.31.x; no MIME validation; bare `except Exception` |
| DevOps Engineer | CI/CD broken | Points to obsolete GCP paths; no dependency scanning; Python 3.11 in CI vs 3.10 in runtime |
| Sys Admin | Temp files not cleaned on success | Cleanup only in error path; `maxConcurrentInstances=1` bottleneck |
| Researcher | Docs up to date | Root requirements.txt has GCP deps; CVE in requests + ptb |
| QA Engineer | 247 tests, critical gaps | 0 tests for admin commands (12 handlers), 0 for audio-processor handler |

---

## Priority Ladder

**Safety > Correctness > Security > Reliability > Simplicity > Cost**

---

## Tier 1 — Critical (Week 1)

| # | Improvement | File | Impact |
|---|-------------|------|--------|
| 1.1 | **Fix balance deduction order** — deduct BEFORE delivery in webhook-handler | `webhook-handler/main.py` | Eliminates free transcriptions on failures |
| 1.2 | **Temp file cleanup in finally** — move os.remove from error path | `audio-processor/handler.py` | Prevents /tmp overflow crash |
| 1.3 | **Fix CI/CD** — redirect to `alibaba/tests/`, Python 3.10 | `.github/workflows/ci.yml` | Regressions caught automatically |
| 1.4 | **Update dependencies** — requests>=2.32.0, ptb>=20.3 | `requirements.txt` (x2) | Closes CVE-2024-35195, token leak |

**Estimate**: 8-12 hours, 0 breaking changes

### Verification (Tier 1)
1. **Balance deduction**: unit test — mock DB failure after deduct, verify balance deducted
2. **Temp cleanup**: unit test — success path, verify `os.remove` called in finally
3. **CI/CD**: push → verify GitHub Actions green, tests run against `alibaba/`
4. **Dependencies**: `pip-audit` → 0 known vulnerabilities

---

## Tier 2 — Important (Weeks 2-3)

| # | Improvement | File | Impact |
|---|-------------|------|--------|
| 2.1 | **Command dispatch pattern** — extract 16 commands into separate handler functions | `webhook-handler/main.py` | Testability, readability (-200 LOC from one function) |
| 2.2 | **Admin command tests** — 20+ tests for /credit, /user, /stat, /mute etc. | `tests/test_admin_commands.py` | Coverage 0% → 80% for admin logic |
| 2.3 | **Audio-processor tests** — 15 tests for handler(), process_job() | `tests/test_audio_processor.py` | Coverage 0% → 70% for async pipeline |
| 2.4 | **Telegram.py tests** — send_as_file, send_long_message, error paths | `tests/test_telegram.py` | Coverage 12% → 60% for delivery logic |
| 2.5 | **Trace ID** — add correlation ID in job_data → MNS → audio-processor → logs | `shared/utility.py`, `mns_service.py` | End-to-end debugging, lower MTTR |
| 2.6 | **DashScope response validation** — check structure before access | `shared/audio.py` | Prevents opaque KeyError in production |

**Estimate**: 20-30 hours, 0 breaking changes

### Verification (Tier 2)
1. `pytest alibaba/tests/ -v --cov=alibaba/shared --cov-report=html` → coverage >= 65%
2. Deploy → curl webhook → verify trace_id in logs of both services

---

## Tier 3 — Improvements (Week 4+)

| # | Improvement | File | Impact |
|---|-------------|------|--------|
| 3.1 | **Decompose process_job** (284 LOC → 5 functions) | `audio-processor/handler.py` | Testability, readability |
| 3.2 | **Rate limiting** — 10 cmd/sec per user | `webhook-handler/main.py` | Abuse/self-DoS protection |
| 3.3 | **Remove dead code** — `format_text_with_gemini()` | `shared/audio.py` | -43 LOC, cleaner codebase |
| 3.4 | **maxConcurrentInstances=2** for audio-processor | `s.yaml` | +50% throughput at peak (+$2/mo) |
| 3.5 | **Cost attribution metrics** — tokens, minutes, API calls per user | `shared/audio.py`, `shared/utility.py` | Unit economics for investors |
| 3.6 | **MIME validation** for audio files before OSS upload | `shared/audio.py` | Security (blocks malware upload) |
| 3.7 | **Specific exceptions** instead of bare `except Exception` | All handler files | Faster deployment/auth bug discovery |
| 3.8 | **Pre-checkout validation** — check payload structure | `webhook-handler/main.py` | Prevents opaque payment failures |
| 3.9 | **MNS tests** — publish failure, retry, message format | `tests/test_mns_service.py` | Coverage 17% → 60% |
| 3.10 | **pytest.ini + coverage** — gate >= 70%, HTML report | `alibaba/pytest.ini` | Coverage visibility, regression guard |

**Estimate**: 30-40 hours

### Verification (Tier 3)
1. `pytest alibaba/tests/ -v --cov=alibaba/shared --cov-report=html` → coverage >= 70%
2. Grep for `except Exception` → should remain only in top-level handlers
3. Load test: 10 concurrent audio → verify no duplicate billing, no /tmp overflow

---

## Metrics Projection

| Metric | Current | After Tier 1 | After Tier 2 | After Tier 3 |
|--------|---------|-------------|-------------|-------------|
| **Tests** | 247 | 247 | ~330 | ~400 |
| **Coverage (est.)** | ~45% | ~48% | ~65% | ~78% |
| **CVE** | 2 | 0 | 0 | 0 |
| **CI/CD** | broken | working | + coverage | + Snyk |
| **Max function LOC** | 284 | 284 | 243 | ~60 |
| **Billing bug** | yes | fixed | fixed | fixed |
| **Temp cleanup** | missing | done | done | done |
| **Tracing** | none | none | trace_id | trace_id |

---

## Panel Conflicts & Resolutions

| Topic | Position A | Position B | Resolution |
|-------|-----------|-----------|------------|
| Refactoring vs features | Architect: refactor now | Business: don't block features | **Tier 1 (balance, cleanup) now; rest — parallel with features** |
| Test coverage | QA: 80+ tests immediately | DevOps: fix CI first | **CI first → then tests (no point writing tests if CI doesn't run them)** |
| maxConcurrentInstances | SysAdmin: increase to 3 | Business: +cost | **Increase to 2 (compromise: +50% throughput, +$2/mo)** |

---

## Open Questions

1. How many free transcriptions already occurred due to the balance bug?
2. Is proxy used in production? (if yes — CVE-2024-35195 is critical)
3. Any double-billing cases observed in production?
4. What is /tmp size on FC instance?
5. Is there a staging environment or only production?
6. Should we adopt `pyproject.toml` for dependency management?

---

*Priority Ladder: Safety > Correctness > Security > Reliability > Simplicity > Cost*
