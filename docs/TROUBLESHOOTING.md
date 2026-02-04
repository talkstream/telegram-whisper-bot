# Troubleshooting

Решение частых проблем Telegram Whisper Bot.

## Ошибки ASR

### "Model not found" / "InvalidParameter"

**Причина:** Использование устаревшей модели (paraformer-v1, paraformer-v2).

**Решение:** Используйте `qwen3-asr-flash`:

```python
# Правильно
model = "qwen3-asr-flash"
endpoint = "https://dashscope-intl.aliyuncs.com/api/v1"

# Неправильно
model = "paraformer-v1"  # Deprecated!
```

### "Connection timeout" / "Failed to connect"

**Причина:** Неправильный endpoint DashScope.

**Решение:** Используйте international endpoint:

```python
# Правильно (для eu-central-1)
endpoint = "https://dashscope-intl.aliyuncs.com/api/v1"

# Неправильно
endpoint = "https://dashscope.aliyuncs.com/api/v1"  # China only!
```

### "400 Bad Request" при транскрипции

**Причина:** Использование WebSocket API вместо REST.

**Решение:** Используйте REST API:

```python
# REST API endpoint
url = "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription"

response = requests.post(url, headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}, json={
    "model": "qwen3-asr-flash",
    "input": {"file_urls": [audio_url]}
})
```

### "Audio too short" / "Insufficient audio"

**Причина:** Аудио короче 0.5 секунды.

**Решение:** Проверяйте длительность перед отправкой:

```python
if duration_seconds < 0.5:
    return "Аудио слишком короткое для транскрипции"
```

## Ошибки Tablestore

### "OTSConditionCheckFail"

**Причина:** Row не существует при EXPECT_EXIST или уже существует при EXPECT_NOT_EXIST.

**Решение:** Используйте правильный Condition:

```python
# Для создания новой записи
condition = Condition(RowExistenceExpectation.EXPECT_NOT_EXIST)

# Для обновления существующей
condition = Condition(RowExistenceExpectation.EXPECT_EXIST)

# Если не важно
condition = Condition(RowExistenceExpectation.IGNORE)
```

### "Invalid update_columns format"

**Причина:** Использование `'PUT'` вместо `'put'` (uppercase).

**Решение:** Всегда lowercase:

```python
# Правильно
update_columns = {'put': [('balance_minutes', 100)]}

# Неправильно
update_columns = {'PUT': [('balance_minutes', 100)]}  # Error!
```

### "Primary key mismatch"

**Причина:** Несоответствие типа primary key.

**Решение:** user_id всегда string:

```python
# Правильно
primary_key = [('user_id', str(user_id))]

# Неправильно
primary_key = [('user_id', user_id)]  # Если user_id это int
```

## Ошибки MNS

### "Queue not found"

**Причина:** Очередь не создана или неправильное имя.

**Решение:**
1. Проверьте имя очереди в MNS Console
2. Убедитесь что регион совпадает (eu-central-1)

### "Message too large"

**Причина:** Сообщение > 64KB.

**Решение:** Передавайте только метаданные, не аудио:

```python
# Правильно
message = {
    "job_id": job_id,
    "user_id": user_id,
    "file_id": file_id  # Telegram file_id, не bytes
}

# Неправильно
message = {
    "audio_data": base64.encode(audio_bytes)  # Too large!
}
```

### Jobs зависают в "processing"

**Причина:** Worker упал во время обработки.

**Решение:**
```
/status   # Проверить состояние
/flush    # Очистить зависшие задачи
```

## Ошибки Telegram

### "Bad Request: message is too long"

**Причина:** Сообщение > 4096 символов.

**Решение:** Разбивайте на части:

```python
MAX_LENGTH = 4000

if len(text) > MAX_LENGTH:
    chunks = [text[i:i+MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
    for chunk in chunks:
        await send_message(chat_id, chunk)
else:
    await send_message(chat_id, text)
```

### "Forbidden: bot was blocked by the user"

**Причина:** Пользователь заблокировал бота.

**Решение:** Игнорируйте ошибку, не повторяйте отправку:

```python
try:
    await send_message(chat_id, text)
except TelegramError as e:
    if "blocked by the user" in str(e):
        logger.info(f"User {chat_id} blocked the bot")
        return  # Don't retry
    raise
```

### "File is too big"

**Причина:** Файл > 20MB (лимит Telegram Bot API).

**Решение:** Показывайте понятную ошибку:

```python
if file_size > 20 * 1024 * 1024:
    return "Файл слишком большой. Максимум 20 МБ."
```

## Ошибки FFmpeg

### "No such file or directory"

**Причина:** FFmpeg не установлен в FC runtime.

**Решение:** Добавьте layer с FFmpeg или используйте custom runtime.

### "Invalid data found when processing input"

**Причина:** Повреждённый аудиофайл.

**Решение:** Проверяйте файл перед обработкой:

```python
result = subprocess.run(
    ['ffprobe', '-v', 'error', input_path],
    capture_output=True
)
if result.returncode != 0:
    return "Не удалось обработать файл. Возможно, он повреждён."
```

### "Output file is empty"

**Причина:** Неподдерживаемый формат или кодек.

**Решение:** Используйте универсальные параметры:

```bash
ffmpeg -y -i input -b:a 32k -ar 16000 -ac 1 output.mp3
```

## Ошибки деплоя

### "Function not found"

**Причина:** Неправильное имя функции в s.yaml.

**Решение:** Проверьте соответствие имён:

```yaml
services:
  webhook-handler:    # Это имя функции
    component: fc3
```

### "Timeout during deployment"

**Причина:** Большой размер пакета или медленное соединение.

**Решение:**
1. Исключите ненужные файлы в `.fcignore`
2. Используйте `--use-remote` для remote build

### "Permission denied"

**Причина:** Недостаточно прав у AccessKey.

**Решение:** Добавьте политики:
- `AliyunFCFullAccess`
- `AliyunOTSFullAccess`
- `AliyunMNSFullAccess`

## Ошибки производительности

### Медленный cold start (>5 сек)

**Причины:**
1. Большой размер пакета
2. Много импортов

**Решения:**
1. Минимизируйте зависимости
2. Используйте lazy imports
3. Включите provisioned concurrency (платно)

### Высокая задержка обработки

**Причины:**
1. Большой файл
2. Сетевые задержки

**Решения:**
1. Оптимизируйте FFmpeg параметры
2. Используйте региональные endpoints
3. Увеличьте память функции (больше CPU)

## Диагностика

### Проверка конфигурации

```bash
# Проверить переменные
echo $DASHSCOPE_API_KEY
echo $TELEGRAM_BOT_TOKEN
echo $TABLESTORE_ENDPOINT

# Проверить webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"

# Проверить DashScope
curl -X POST "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription" \
  -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3-asr-flash", "input": {"file_urls": ["test"]}}'
```

### Логи

```bash
# Webhook handler
s logs webhook-handler --tail

# Audio processor
s logs audio-processor --tail

# Фильтр по времени
s logs webhook-handler --start-time "2026-02-04 10:00:00"
```

### Админ-команды

```
/status    # Состояние очереди
/metrics   # Производительность
/cost      # Расходы
```

---

*Версия: v3.3.0*
