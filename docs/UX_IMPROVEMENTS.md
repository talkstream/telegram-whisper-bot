# UI/UX Patterns

## Evolving Progress Messages (v3.4.0)

Single message that updates through stages via `edit_message_text`:

### Sync Path (< 60 sec audio)

```
"🎙 Аудио получено. Обрабатываю..."   ← initial (webhook)
  → "📥 Загружаю файл..."              ← edit + typing
  → "🎙 Распознаю речь..."             ← edit + typing
  → "✏️ Форматирую текст..."           ← edit + typing (if text > 100 chars)
  → [result text]                       ← edit (or delete+send if > 4000 chars)
```

### Async Path (>= 60 sec audio)

```
"🎙 Аудио получено. Обрабатываю..."   ← initial (webhook)
  → "⏳ Аудио в очереди..."            ← edit (webhook)
  → "🔄 Обработка началась..."          ← edit (processor)
  → "📥 Загружаю файл..."              ← edit + typing
  → "🎙 Распознаю речь..."             ← edit + typing
  → "✏️ Форматирую текст..."           ← edit + typing
  → [result text]                       ← edit (or delete+send if > 4000 chars)
```

### Implementation Details

- `status_message_id` flows: webhook → `job_data` → MNS → audio-processor
- Pattern: `edit_message_text(stage)` → `send_chat_action('typing')` → heavy work
- Typing indicator visible during heavy operations, not before edits
- LLM formatting skipped for text <= 100 chars

## Progress Stages

| Emoji | Stage | Duration |
|-------|-------|----------|
| 🎙 | Received | instant |
| ⏳ | Queued | async only |
| 🔄 | Processing started | async only |
| 📥 | Downloading | 0.2-3s |
| 🎙 | Transcribing (ASR) | 2-10s |
| ✏️ | Formatting (LLM) | 2-5s |

## Edge Cases

- **Text > 4000 chars:** delete status message, send new one
- **No status_message_id:** create new progress message (backward compat)
- **MNS fallback to sync:** status_message_id passed through

## Telegram API Limits

- Message edits: ~30/min per chat
- Update interval: 3 sec minimum
- `send_chat_action`: lasts 5 seconds, fire-and-forget (timeout=2s)

## Best Practices

1. **Immediate acknowledgment**: confirm file receipt instantly
2. **Evolving messages**: one message, multiple edits (no chat spam)
3. **Typing between stages**: fill silence during heavy operations
4. **Graceful degradation**: if edit fails, send new message
