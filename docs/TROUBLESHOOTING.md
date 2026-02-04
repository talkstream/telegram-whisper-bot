# Troubleshooting

## ASR Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Model not found | Deprecated model | Используйте `qwen3-asr-flash` |
| Connection timeout | Wrong endpoint | Используйте `dashscope-intl.aliyuncs.com` |
| 400 Bad Request | WebSocket вместо REST | REST API: `/api/v1/services/audio/asr/transcription` |
| Audio too short | < 0.5 сек | Проверяйте длительность перед отправкой |

**Correct ASR config:** см. [ALIBABA_CRITICAL_CONFIG.md](ALIBABA_CRITICAL_CONFIG.md)

## Tablestore Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| OTSConditionCheckFail | Row exists/not exists mismatch | Используйте правильный Condition |
| Invalid update_columns | `'PUT'` вместо `'put'` | **Всегда lowercase `'put'`** |
| Primary key mismatch | int вместо str | `[('user_id', str(user_id))]` |

**Паттерн update_row:**
```python
update_columns = {'put': [('balance_minutes', 100)]}  # lowercase!
```

**Conditions:**
- `EXPECT_NOT_EXIST` — создание
- `EXPECT_EXIST` — обновление
- `IGNORE` — upsert

## MNS Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Queue not found | Неправильное имя/регион | Проверьте MNS Console |
| Message too large | > 64KB | Передавайте только metadata, не audio bytes |
| Jobs stuck processing | Worker crashed | `/status` → `/flush` |

## Telegram Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Message too long | > 4096 chars | Разбивайте на chunks по 4000 |
| Bot blocked by user | User blocked bot | Игнорируйте, не retry |
| File too big | > 20MB | Показывайте ошибку пользователю |

## FFmpeg Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| No such file | FFmpeg не установлен | Добавьте layer с FFmpeg |
| Invalid data | Повреждённый файл | `ffprobe -v error` перед обработкой |
| Empty output | Неподдерживаемый кодек | `-b:a 32k -ar 16000 -ac 1` |

**FFmpeg commands:** см. [ALIBABA_CRITICAL_CONFIG.md](ALIBABA_CRITICAL_CONFIG.md#ffmpeg)

## Deploy Errors

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Function not found | Неправильное имя в s.yaml | Проверьте `services:` имена |
| Timeout | Большой пакет | `.fcignore` или `--use-remote` |
| Permission denied | Недостаточно прав | `AliyunFCFullAccess`, `AliyunOTSFullAccess`, `AliyunMNSFullAccess` |

## Performance Issues

| Проблема | Причины | Решения |
|----------|---------|---------|
| Cold start > 5s | Большой пакет, много imports | Минимизируйте deps, lazy imports |
| Slow processing | Большой файл, сетевые задержки | Оптимизируйте FFmpeg, regional endpoints, больше memory |

## Диагностика

### Проверка конфигурации

```bash
# Переменные
echo $DASHSCOPE_API_KEY && echo $TELEGRAM_BOT_TOKEN

# Webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"

# DashScope
curl -X POST "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription" \
  -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-asr-flash", "input": {"file_urls": ["test"]}}'
```

### Логи

```bash
s logs webhook-handler --tail
s logs audio-processor --tail
s logs webhook-handler --start-time "2026-02-04 10:00:00"
```

### Админ-команды

```
/status   # Queue state
/metrics  # Performance
/cost     # Costs
```

---

*v3.3.0*
