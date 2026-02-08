# UX Patterns

## Progress Messages (v3.4.0)

Single message updated through stages via `edit_message_text`.

### Sync (< 60s audio)
```
"🎙 Аудио получено..."
  → "📥 Загружаю файл..."         + typing
  → "🎙 Распознаю речь..."        + typing
  → "✏️ Форматирую текст..."      + typing (if >100 chars)
  → [result]                       edit or delete+send
```

### Async (≥ 60s audio)
```
"🎙 Аудио получено..."
  → "⏳ В очереди..."              webhook
  → "🔄 Обработка..."             processor
  → "📥 Загружаю..."   → "🎙 Распознаю..."   → "✏️ Форматирую..."
  → [result]
```

### Diarization Path (v3.6.0)
```
"🎙 Аудио получено..."
  → "📤 Загружаю для анализа..."   OSS upload
  → "🔄 Распознаю с диаризацией..." poll every 5s (max 5min)
  → "✏️ Форматирую текст..."
  → [dialogue with em-dashes]
```

Fallback: if diarization fails → regular ASR path (transparent to user).

## Delivery Modes (v3.6.0)

| Condition | Action |
|-----------|--------|
| ≤4000 chars | Edit status message in place |
| >4000, `long_text_mode: split` | Delete status → `send_long_message()` |
| >4000, `long_text_mode: file` | Delete status → send .txt with caption |

## Implementation

- `status_message_id` flows: webhook → `job_data` → MNS → processor
- Pattern: `edit_message_text(stage)` → `send_chat_action('typing')` → work
- Typing visible during heavy ops, not before edits

## Telegram API Limits

- Message edits: ~30/min per chat
- Min edit interval: 3s
- `send_chat_action`: 5s duration, fire-and-forget (2s timeout)

## Principles

1. Immediate acknowledgment on file receipt
2. Evolving single message (no chat spam)
3. Typing between stages
4. Graceful degradation: edit fails → send new

---

*v4.0.0*
