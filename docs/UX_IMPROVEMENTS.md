# UX Patterns

Delivery logic summary: see [CLAUDE.md#key-patterns](../CLAUDE.md#key-patterns)

## Progress Messages

Single message updated through stages via `edit_message_text`.

### Sync (< 60s audio)

```
"Аудио получено..."
  → "Загружаю файл..."         + typing
  → "Распознаю речь..."        + typing
  → "Форматирую текст..."      + typing (if >100 chars)
  → [result]                    edit | delete+send
```

### Async (>= 60s audio)

```
"Аудио получено..."
  → "В очереди..."              webhook
  → "Обработка..."              processor
  → "Загружаю..." → "Распознаю..." → "Форматирую..."
  → [result]
```

### Diarization Path

```
"Аудио получено..."
  → "Загружаю для анализа..."   OSS upload
  → "Распознаю с диаризацией..." poll 5s (max 5min)
  → "Форматирую текст..."
  → [dialogue with em-dashes]
```

Fallback: diarization fail → regular ASR (transparent to user).

## Delivery Modes

| Condition | Action |
|-----------|--------|
| <=4000 chars | Edit status message in place |
| >4000, split mode | Delete status → `send_long_message()` |
| >4000, file mode | Delete status → send .txt with caption |

## Implementation

- `status_message_id` flows: webhook → `job_data` → MNS → processor
- Pattern: `edit_message_text(stage)` → `send_chat_action('typing')` → work

## Telegram API Limits

| Limit | Value |
|-------|-------|
| Message edits | ~30/min per chat |
| Min edit interval | 3s |
| `send_chat_action` | 5s duration, fire-and-forget (2s timeout) |

## Principles

1. Immediate acknowledgment on file receipt
2. Evolving single message (no chat spam)
3. Typing between stages
4. Graceful degradation: edit fails → send new

---

*v4.0.0*
