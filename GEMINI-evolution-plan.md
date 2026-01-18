# Telegram Whisper Bot - Evolution Plan (v3.1)

**Date:** 2026-01-17
**Status:** Active Development
**Goal:** Unify architecture and migrate to high-performance Async I/O.

---

## 🛑 Phase 1: Critical Architecture & Security (Completed)
- **1.1 Fix Circular Dependencies:** ✅ DONE
- **1.2 Webhook Security Hardening:** ✅ DONE
- **1.3 Async I/O Preparation:** ✅ PARTIAL (Session tuning implemented)

## ⚡ Phase 2: Performance & Caching (Completed)
- **2.1 In-Memory User Cache:** ✅ DONE
- **2.2 Smart Audio Caching:** ✅ DONE

## 🛠 Phase 3: Robustness & Observability (Completed)
- **3.1 Structured Logging (JSON):** ✅ DONE
- **3.2 Robust FFmpeg Parsing:** ✅ DONE
- **3.3 Enhanced Progress Feedback:** ✅ DONE

---

## 🚀 Phase 4: Refactoring & Unification (Current Focus)

### 4.1 Code Base Unification
- **Status:** **DONE** (2026-01-17)
- **Goal:** Eliminate code duplication between Bot and Worker.
- **Action:**
    1.  Create a `shared` python package containing `services`, `config`, and `models`.
    2.  Refactor `Dockerfile` and `cloudbuild.yaml` to install this local package into both images.
    3.  Remove `audio-processor-deploy/services`.

### 4.2 FastAPI & Aiogram Migration (The "Async Leap")
- **Status:** **DONE** (2026-01-17)
- **Goal:** Non-blocking I/O for high scalability.
- **Action:**
    1.  Replace **Flask** with **FastAPI**.
    2.  Replace raw `requests` and `telebot` logic with **aiogram 3.x** (native async).
    3.  Re-implement `app/routes.py` as FastAPI endpoints (`/webhook`, `/health`, `/cron`).
    4.  Migrate `FirestoreService` to use async/await where possible (or run in threadpool).

### 4.3 Dependency Injection
- **Goal:** Testable, loosely coupled components.
- **Action:** Use FastAPI's `Depends` system to inject services (`AudioService`, `FirestoreService`) into route handlers.

---

## 🔮 Phase 5: Future Enhancements (Post-Refactor)
- **Queue Priority:** Separate queues for "Premium" vs "Free" users.
- **Regional Expansion:** Deploy Workers in multiple regions.
- **Analytics Dashboard:** Web UI for stats (using the new FastAPI backend).