# UI/UX Patterns

## Progress Stages

| Progress | Emoji | Stage |
|----------|-------|-------|
| 0-19% | 🔄 | Starting |
| 20-34% | 📥 | Downloading |
| 35-49% | 🔧 | Converting |
| 50-79% | 🎙 | Transcribing |
| 80-94% | ✨ | Formatting |
| 95-99% | 📤 | Sending |
| 100% | ✅ | Done |

## Progress UI Template

```
🎙 Распознавание...

[▓▓▓▓▓▓░░░░] 60%

⏱ Осталось: ~25 сек.
```

---

## Graceful Degradation Messages

### Cold Start
```
🖥 Запускаю сервер...

Это может занять до 1 минуты при первом запуске.
Последующие запросы будут быстрее.
```

### Long Audio Warning
```
📢 Длинная запись (15 мин.)

Обработка займёт ~22 мин.
Вы получите результат, как только он будет готов.
```

### Fallback
```
🔄 Переключаюсь на быструю обработку...

GPU-сервер занят, использую облачный API.
```

### Queue Position
```
📋 Ваш запрос в очереди

Позиция: 3
Ожидание: ~2 мин.
```

---

## Telegram API Limits

- Message edits: ~30/min per chat
- Update interval: 3 sec minimum
- Max updates/min: 20 (safe margin)

---

## Best Practices

1. **Progress Indicators**: Visual bar + time estimate + stage name
2. **Error Messages**: Clear, non-alarming, actionable
3. **Immediate Acknowledgment**: Confirm file receipt instantly
4. **Graceful Degradation**: Explain delays, offer alternatives
