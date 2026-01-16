# План рефакторинга и оптимизации Telegram Whisper Bot (v2.1)

**Статус:** ✅ COMPLETE (16.01.2026).

## Часть 1: Оптимизация DevOps (Приоритет: Высокий)

**Проблема:** Сборка `audio-processor` занимает 15 минут. Синхронизация кода ручная и ненадежная.
**Решение:** Внедрение Google Cloud Build (`cloudbuild.yaml`) для стандартизации CI/CD.

1.  **[DONE] Создание `Dockerfile.base`:**
    *   Содержит: OS, Dependencies, FFmpeg 8.0 (compiled), Whisper.cpp, Whisper Model.
    *   Сборка: `gcr.io/editorials-robot/audio-processor-base:v1`.
2.  **[DONE] Конфигурация `cloudbuild.base.yaml`:**
    *   Декларативное описание сборки базового образа.
    *   Запуск: `gcloud builds submit --config cloudbuild.base.yaml .`
3.  **[DONE] Конфигурация `cloudbuild.app.yaml`:**
    *   Автоматическая синхронизация папки `services/` перед сборкой.
    *   Сборка легкого образа приложения (`Dockerfile` from base).
    *   Автоматический деплой в Cloud Run.
4.  **[DONE] Запуск сборки базового образа** (выполнено 16.01.2026).

## Часть 2: Архитектурный Рефакторинг (Приоритет: Высокий)

**Проблема:** `main.py` ("Bot") выполняет тяжелую конвертацию видео, вызывая тайм-ауты и требуя наличия FFmpeg в контейнере бота.
**Решение:** Перенос всей обработки медиа в `audio-processor`.

1.  **[DONE] Модификация `main.py`:**
    *   Удалена функция `process_video_file` (и локальный вызов ffmpeg).
    *   Для видео-файлов: просто публикуется задача в Pub/Sub.
2.  **[DONE] Модификация `audio_processor.py` и `services/audio.py`:**
    *   `services/audio.py` обновлен для использования FFmpeg 8.0 Whisper вместо OpenAI API.
    *   Worker использует базовый образ с FFmpeg 8.0.
    *   Логика обработки видео и аудио теперь полностью на стороне Worker.

## Часть 3: Надежность и Обработка Ошибок (Приоритет: Средний)

**Проблема:** "Сглатывание" ошибок (всегда 200 OK) и ненадежный парсинг логов.

1.  **[DONE] Dead Letter Queue (DLQ):**
    *   Настроен топик `audio-processing-dlq`.
    *   В `audio_processor.py` реализована логика `RetryableError` (500 -> Retry -> DLQ) и Permanent Error (200 OK).
2.  **[DONE] JSON Parser для FFmpeg:**
    *   Добавлена базовая поддержка JSON парсинга в `_parse_ffmpeg_whisper_output`.

## Часть 4: Очистка (Приоритет: Низкий)

1.  **[DONE]** Удален легаси-код (`main.py.old`).
2.  **[DONE]** Унифицирована структура папок (через Cloud Build sync).

---

## Итог

Система переведена на архитектуру v2.1:
- **Worker:** FFmpeg 8.0 (Whisper) + Gemini 2.0 Flash (Experimental).
- **DevOps:** Base Image + Cloud Build CI/CD.
- **Reliability:** Retry logic + DLQ.
- **Cost:** OpenAI API removed ($0).