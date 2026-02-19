# Журналистский пайплайн: длинное аудио, smart chunking, large file upload

## Context

Запрос от журналиста на использование сервиса. Типичные сценарии: интервью (15-30 мин, 2 спикера), пресс-конференции (30-90 мин, 3-10 спикеров), совещания (30-60 мин, 2-5 спикеров).

**Текущие ограничения:**
- LLM truncation: Gemini max_tokens=8192 → обрезает текст >30k chars, возвращает неотформатированный оригинал
- FC timeout: 300s — рискованно для 30+ мин аудио (diarization 60-180s + LLM 47s + download)
- Telegram: 20MB лимит на скачивание файлов — длинные записи в хорошем качестве не пролезают
- Весь транскрипт отправляется в LLM одним запросом — нет chunk-форматирования
- Прогресс: одно сообщение «Обработка началась...», нет ETA, нет стадий

---

## Tier 1: Critical (unblocks journalist use)

### 1.1 FC timeout: 300s → 600s

**File:** `alibaba/s.yaml`

Изменить `timeout: 300` → `timeout: 600` для audio-processor. FC 3.0 поддерживает до 86400s. Стоимость: 0 (FC тарифицирует фактическое время, не ceiling).

### 1.2 Smart semantic chunking для LLM

**File:** `alibaba/shared/audio.py` — новые методы в `AudioService`

**Принцип:** чанки крупные (~4000 chars), по смыслу, с синхронизацией по спикерам, без разрывов посреди фраз.

**Константа:** `LLM_CHUNK_THRESHOLD = 4000` chars (~600 слов RU, гарантированно помещается в 8192 output tokens)

#### Стратегия для диалога (`is_dialogue=True`):

Текст уже структурирован как `Спикер N:\n— реплика`. Splitting идёт по границам смены спикера (regex `^Спикер \d+:` с `re.MULTILINE`). Группируем несколько последовательных спикерских блоков в один чанк до достижения `LLM_CHUNK_THRESHOLD`. Если единичный блок спикера > threshold — делим внутри по предложениям (`. `, `! `, `? `).

Каждый чанк кроме первого получает 1-строчный контекст: последняя реплика предыдущего чанка (помечена `[...]`) — LLM понимает контекст, но не дублирует при reassembly.

#### Стратегия для монолога (`is_dialogue=False`):

Splitting по абзацам (`\n\n`), затем по предложениям. Каждый чанк кроме первого получает последнее предложение предыдущего чанка как overlap (помечено `[...]`). При reassembly overlap удаляется.

#### Новые методы:

```python
LLM_CHUNK_THRESHOLD = 4000

def _split_for_llm(self, text: str, is_dialogue: bool) -> list[str]:
    """Split text into semantic chunks preserving speaker/paragraph structure."""

def _format_text_chunked(self, text, use_code_tags, use_yo, is_chunked,
                          is_dialogue, backend, progress_callback=None) -> str:
    """Format long text chunk-by-chunk, reassemble."""
```

#### Модификация `format_text_with_llm()`:

```python
def format_text_with_llm(self, text, ..., progress_callback=None):
    if len(text) > self.LLM_CHUNK_THRESHOLD:
        logging.info(f"[llm] chunking: {len(text)} chars, backend={backend}")
        return self._format_text_chunked(...)
    # existing single-call path unchanged
```

`progress_callback(current, total)` — вызывается для каждого чанка, пробрасывается из handler.py для обновления Telegram progress message.

### 1.3 Auto-file delivery для длинных транскриптов

**File:** `alibaba/audio-processor/handler.py` — `_deliver_result()`

```python
AUTO_FILE_THRESHOLD = 8000  # ~2 стр. A4

def _deliver_result(tg, chat_id, progress_id, formatted_text, settings, is_dialogue=False):
    # Если текст > 8000 chars → автоматически файл
    # Если диалог → caption включает кол-во спикеров
    # Filename: transcript_YYYY-MM-DD_HHMM.txt
```

**File:** `alibaba/shared/telegram.py` — `send_as_file()` — добавить `filename_hint` параметр.

---

## Tier 2: Important (quality & UX)

### 2.1 ProgressManager с ETA

**File:** `alibaba/audio-processor/handler.py` — новый класс

```python
class ProgressManager:
    MIN_UPDATE_INTERVAL = 3  # Telegram rate limit

    def __init__(self, tg, chat_id, message_id): ...
    def update(self, text, force=False): ...
    def stage(self, stage_key, **kwargs): ...
```

Стадии с ETA:
- `📥 Загружаю файл...`
- `🎙 Распознаю речь...` / `🎙 Распознаю речь... (часть N из M)`
- `🔄 Распознаю спикеров... (~2-3 мин)`
- `🔄 Объединяю результаты...`
- `✏️ Форматирую текст... (часть N из M)`
- `📤 Отправляю результат...`

ETA рассчитывается по длительности аудио:
- <5 мин → ~1 мин
- 5-30 мин → ~2-3 мин
- 30-60 мин → ~3-5 мин
- 60+ мин → ~5-8 мин

### 2.2 Time budget watchdog

**File:** `alibaba/audio-processor/handler.py` — в `process_job()`

```python
FC_TIMEOUT = int(os.environ.get('FC_TIMEOUT', '600'))
SAFETY_MARGIN = 30
deadline = time.monotonic() + FC_TIMEOUT - SAFETY_MARGIN

# Перед LLM:
remaining = deadline - time.monotonic()
if remaining < 60:
    logging.warning(f"[watchdog] low time budget ({remaining:.0f}s), skipping LLM")
    formatted_text = text  # лучше неотформатированный, чем timeout
```

### 2.3 Quality safeguards для длинного аудио

**File:** `alibaba/shared/audio.py`

**a) Windowed timeline normalization** (для аудио >15 мин):
Вместо глобального масштабирования — 5-минутные окна с 50% overlap. Корректирует локальный drift между pass1 и pass2.

```python
def _normalize_windowed(self, speaker_segments, text_segments, window_ms=300000):
    """Scale timelines per 5-min window to correct local drift."""
```

**b) Gap ratio detection:**
После alignment считаем долю слов, не попавших ни в один speaker segment. Если >30% — diarization плохая, возвращаем raw text без спикеров.

**c) Micro-segment filter:**
Сегменты <500ms с ≤2 словами — шум diarization. Мержим с соседним сегментом.

### 2.4 Dynamic diarization timeout

**File:** `alibaba/shared/audio.py` — `transcribe_with_diarization()`

Параметризовать timeout (сейчас hardcoded 270s):
- <30 мин аудио → 180s
- 30-60 мин → 240s
- 60+ мин → 300s

---

## Tier 3: Innovation (large file upload, UI/UX)

### 3.1 Telegram Mini App для загрузки файлов >20MB

**Архитектура:**

```
Пользователь → /upload → бот отправляет кнопку Web App
→ Mini App HTML (отдаётся из FC webhook GET handler)
→ Пользователь выбирает файл в браузере (до 2GB)
→ Mini App запрашивает PUT signed URL (POST /api/signed-url)
→ OSS генерирует PUT signed URL (oss2.sign_url('PUT', key, 3600))
→ Файл загружается НАПРЯМУЮ в OSS (клиент → OSS, минуя FC)
→ Mini App уведомляет бота (POST /api/process {oss_key, user_id})
→ audio-processor получает задачу, скачивает из OSS, обрабатывает
```

**Существующая инфраструктура:**
- OSS bucket: `twbot-prod-audio` — уже используется для diarization
- `oss2` SDK: уже установлен, `bucket.sign_url('PUT', oss_key, expiry)` доступен
- HTTP triggers: webhook уже поддерживает GET + POST anonymous
- FC может вернуть HTML из GET handler (изменить `Content-Type: text/html`)

**Компоненты:**
1. **Mini App HTML** (~200 строк): drag&drop zone, progress bar, формат-валидация на клиенте
2. **API endpoint `/api/signed-url`** в webhook-handler: генерация PUT signed URL
3. **API endpoint `/api/process`**: создание job без Telegram file_id (из OSS key)
4. **Команда `/upload`**: отправляет кнопку `web_app` с URL Mini App

**Лимиты OSS:**
- Direct PUT: до 5GB
- Signed URL TTL: 1 час (достаточно для upload)

### 3.2 Импорт из облачных хранилищ

**Yandex.Disk, Google Drive, iCloud — через публичные ссылки.**

Пользователь отправляет ссылку → бот определяет сервис → скачивает файл:

| Сервис | Формат ссылки | Скачивание |
|--------|---------------|------------|
| Yandex.Disk | `disk.yandex.ru/d/...` | API: `GET https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=URL` → redirect |
| Google Drive | `drive.google.com/file/d/.../view` | `GET https://drive.google.com/uc?export=download&id=FILE_ID` |
| iCloud | `icloud.com/iclouddrive/...` | Не поддерживает direct download без auth → skip |
| Dropbox | `dropbox.com/s/...` | Заменить `dl=0` → `dl=1` в URL |

**Реализация:**
- Новый handler в webhook: `_handle_url_message()` — детектит URL, определяет сервис
- Скачивание в `/tmp/` → далее обычный пайплайн (prepare_audio → transcribe → format)
- Лимит: 100MB (чтобы не забить FC tmpdir)

### 3.3 Экзотические UI/UX варианты (рассмотрены)

| Вариант | Оценка | Вердикт |
|---------|--------|---------|
| **Streaming partial results** — отправлять первые 5 мин пока обрабатываются остальные | Красиво, но ASR chunking не синхронизирован с diarization. Partial results без спикеров бесполезны для журналиста | ❌ Не сейчас |
| **Export в Google Docs** — автоматически создавать документ | Требует Google OAuth, сложная auth flow. Достаточно .txt файла — журналист копирует | ❌ Overkill |
| **QR-код для скачивания** — результат на временном URL | OSS signed GET URL + QR в Telegram. Мгновенный доступ с другого устройства. Cheap win | ✅ Tier 3+ |
| **Голосовое резюме** — бот озвучивает summary | TTS API + LLM summary. Интересно, но вне scope | ❌ Future |
| **Inline keyboard для настроек перед обработкой** — выбор формата, спикеров, языка | Добавляет friction. Текущие defaults оптимальны. `/settings` достаточно | ❌ Не нужно |

---

---

## Прогнозируемые максимумы после доработок

| Параметр | Текущий | После Tier 1+2 | После Tier 3 |
|----------|---------|----------------|--------------|
| **Макс. длительность аудио** | ~60 мин (timeout risk) | **60 мин** (safe, 600s FC) | **120+ мин** (OSS upload) |
| **Макс. размер файла** | 20 MB (Telegram API) | 20 MB | **2 GB** (Mini App → OSS) |
| **Макс. длина транскрипта** | ~30k chars (LLM truncation) | **неограничен** (chunk-LLM) | неограничен |
| **Время обработки 2 мин** | ~73s | ~73s (без изменений) | ~73s |
| **Время обработки 30 мин** | timeout / unformatted | **~3-4 мин** | ~3-4 мин |
| **Время обработки 60 мин** | fail | **~5-8 мин** | ~5-8 мин |
| **Спикеров** | 2-3 (>3 drift) | **2-5** (windowed norm) | 2-10 (AssemblyAI backend) |
| **Видео** | ✅ (FFmpeg extract) | ✅ | ✅ + large files |
| **Форматы** | ogg/mp3/wav/aac/m4a/flac/mp4/mov/webm/mkv | те же | + любые через Mini App |

---

## Биллинг: проверка баланса + тарифная сетка

### Текущие тарифы

| Пакет | Минуты | Stars | Цена/мин | Маржа vs себестоимость |
|-------|--------|-------|----------|------------------------|
| Микро | 10 | 5 | 0.50⭐ | ~70% |
| Старт | 50 | 35 | 0.70⭐ | ~80% |
| Стандарт | 200 | 119 | 0.595⭐ | ~75% |
| Профи | 1000 | 549 | 0.549⭐ | ~73% |
| MAX | 8888 | 4444 | 0.50⭐ | ~70% |

**Себестоимость:** ~$0.003/мин (ASR $0.002 + LLM $0.001 + FC/OSS ~$0.0005). 1 Star ≈ $0.02. Цена 0.50⭐/мин = $0.01/мин → маржа ~70%.

### Доработка: pre-flight balance check с рекомендацией пакета

**Проблема:** сейчас при `balance < duration_minutes` бот говорит «Недостаточно минут, /buy_minutes». Журналист не знает, сколько минут купить.

**Решение:** в `handle_audio_message()` при недостатке баланса — показать:
1. Сколько минут нужно (`duration_minutes`)
2. Сколько не хватает (`deficit = duration_minutes - balance`)
3. Рекомендуемый пакет (минимальный, покрывающий deficit)
4. Inline-кнопку для покупки этого пакета

```python
# В handle_audio_message(), при balance < duration_minutes:
deficit = duration_minutes - balance
recommended = None
for pkg in sorted(PRODUCT_PACKAGES.values(), key=lambda p: p['minutes']):
    if pkg['minutes'] >= deficit:
        recommended = pkg
        break

msg = (
    f"⏱ Аудио: ~{duration_minutes} мин\n"
    f"💰 Ваш баланс: {balance} мин\n"
    f"📊 Не хватает: {deficit} мин\n\n"
)
if recommended:
    msg += f"Рекомендуем: {recommended['title']} ({recommended['minutes']} мин за {recommended['stars_amount']}⭐)"
    # + inline button для покупки
```

### Доработка: тарифы для больших объёмов

Текущий MAX (8888 мин / 4444⭐) уже покрывает ~148 часов. Для интенсивного журналиста (10 интервью/нед × 30 мин = 300 мин/нед = 1200 мин/мес) пакет «Профи» (1000 мин) = 1 месяц.

**Новый пакет для редакций:**

```python
"editorial_3000": {
    "title": "Пакет 'Редакция'",
    "description": "3000 минут транскрибации для редакций",
    "payload": "buy_editorial_3000",
    "stars_amount": 1399,  # 0.467⭐/мин — лучшая цена
    "minutes": 3000
}
```

Маржа: 0.467⭐ × $0.02 = $0.0093/мин vs себестоимость $0.003/мин → маржа 68%. Ок.

### Доработка: balance check для document (duration=0)

**Текущий flow:** документы приходят с `duration=0` → бот пропускает balance check → audio-processor определяет реальную длительность → может отклонить позже.

**Fix:** в webhook-handler для `file_type='document'` — пропускать initial balance check (показать «Определяю длительность...»), но обязательно проверить в audio-processor ПЕРЕД обработкой. Уже реализовано (handler.py:418-429), но добавить рекомендацию пакета и в этот path.

---

## Безопасность

### Существующие меры (сохраняем)
- **Rate limiting:** `_is_rate_limited()` — 10 req/sec per user, OWNER exempt
- **MIME validation:** `_check_mime_type()` — python-magic/mimetypes перед ASR
- **Pre-checkout validation:** payload + currency (XTR) проверка
- **Input sanitization:** file_id, user_id, chat_id — типизация в process_job()
- **Temp cleanup:** finally block удаляет файлы из /tmp/
- **Error isolation:** TelegramErrorHandler с 60s cooldown

### Новые меры для Tier 1+2

| Мера | Где | Что |
|------|-----|-----|
| **Max duration guard** | handler.py, `_transcribe()` | Отклонять аудио >60 мин (текущий hard limit, можно поднять до 120 мин позже) |
| **Chunk count limit** | audio.py, `_split_for_llm()` | Max 20 чанков → для текста >80k chars вернуть без LLM форматирования |
| **Watchdog timeout** | handler.py, `process_job()` | `time.monotonic()` deadline, не signal-based (FC-safe) |
| **LLM output validation** | audio.py, `_format_text_chunked()` | Если output чанка <10% от input → вернуть input (LLM hallucination guard) |
| **Logging: no content** | все файлы | Принцип v4.3.1: логируем только metadata, никогда содержимое |

### Новые меры для Tier 3 (Mini App)

| Мера | Где | Что |
|------|-----|-----|
| **Signed URL expiry** | webhook-handler | PUT URL: 15 мин (не 1 час — минимизировать окно) |
| **File size limit** | Mini App (JS) | Client-side: max 500MB. Server-side: OSS lifecycle policy 1 hour |
| **MIME validation** | Mini App + processor | Client: accept="audio/*,video/*". Server: _check_mime_type() как обычно |
| **Auth: initData** | webhook-handler | Validate Telegram Mini App `initData` hash (HMAC-SHA256 с bot token) |
| **CORS** | webhook-handler | Ответы API endpoints: `Access-Control-Allow-Origin` только для Telegram domains |
| **Rate limit per user** | webhook-handler | Max 3 concurrent uploads per user (Tablestore counter) |
| **OSS cleanup** | OSS lifecycle | Prefix `uploads/`: auto-delete after 2 hours |
| **No directory traversal** | webhook-handler | OSS key = `uploads/{user_id}/{uuid}.{ext}` — никаких user-controlled paths |

---

## Порядок реализации

| # | Что | Scope | Зависимости |
|---|-----|-------|-------------|
| **1** | FC timeout 300→600 | 1 строка s.yaml | — |
| **2** | ProgressManager | ~80 LOC handler.py | — |
| **3** | Smart chunk-LLM | ~120 LOC audio.py + ~20 LOC handler.py | #2 (progress callbacks) |
| **4** | Auto-file delivery | ~40 LOC handler.py + telegram.py | #3 (is_dialogue passthrough) |
| **5** | Time budget watchdog | ~15 LOC handler.py | #1 (знает timeout) |
| **6** | Quality safeguards | ~100 LOC audio.py | — (independent) |
| **7** | Тесты | ~150 LOC test_journalist_pipeline.py | #1-6 |
| **8** | Mini App upload | ~300 LOC (HTML + API endpoints) | Отдельный PR |
| **9** | Cloud drive import | ~80 LOC webhook-handler | Отдельный PR |

**Tier 1 (#1-4):** одним коммитом → deploy → verify с аудио 30+ мин
**Tier 2 (#5-7):** следующим коммитом
**Tier 3 (#8-9):** отдельные PRs, после проверки Tier 1+2

---

## Critical Files

| File | Изменения |
|------|-----------|
| `alibaba/s.yaml` | timeout 300→600 |
| `alibaba/shared/audio.py` | `_split_for_llm()`, `_format_text_chunked()`, `_normalize_windowed()`, gap ratio, micro-segment filter, dynamic diarization timeout, `format_text_with_llm()` + progress_callback |
| `alibaba/audio-processor/handler.py` | `ProgressManager`, watchdog, auto-file delivery, `is_dialogue` passthrough, chunk progress wiring |
| `alibaba/shared/telegram.py` | `send_as_file()` filename_hint |
| `alibaba/webhook-handler/main.py` | (Tier 3) Mini App endpoints, URL import handler |
| `alibaba/tests/test_journalist_pipeline.py` | Тесты для chunk splitting, reassembly, ProgressManager, watchdog, auto-file |

## Верификация

1. `pytest alibaba/tests/ -v` — все тесты
2. Deploy: `cd alibaba && npx @serverless-devs/s deploy -y`
3. Отправить аудио 01:58 → проверить что chunk-LLM не включается (<4000 chars), обычный путь
4. Отправить аудио 30+ мин → проверить:
   - [ ] Текст отформатирован (не raw ASR)
   - [ ] Прогресс обновляется (стадии + ETA)
   - [ ] Результат приходит как .txt файл
   - [ ] Спикеры определены корректно
   - [ ] Нет timeout / truncation
5. `/logs both 10` — проверить pipeline tags в логах
