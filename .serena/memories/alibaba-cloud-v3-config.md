# Alibaba Cloud v3.0.1 Configuration

**Статус:** ✅ РАБОТАЕТ (February 4, 2026)
**Версия:** v3.0.2 (cleanup)

## ASR (Распознавание речи)

### Модель
- **Название:** `qwen3-asr-flash`
- **НЕ ИСПОЛЬЗОВАТЬ:** `qwen3-asr-flash-realtime` (WebSocket, сложен в serverless)
- **НЕ ИСПОЛЬЗОВАТЬ:** `paraformer-realtime-v2` (устаревшая модель 2024)

### API Endpoint
```
POST https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

### Формат запроса
```python
import requests
import base64

url = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

# Audio как base64 data URI
base64_str = base64.b64encode(audio_bytes).decode('utf-8')
data_uri = f"data:audio/mpeg;base64,{base64_str}"

payload = {
    "model": "qwen3-asr-flash",
    "input": {
        "messages": [
            {"role": "system", "content": [{"text": ""}]},
            {"role": "user", "content": [{"audio": data_uri}]}
        ]
    },
    "parameters": {
        "result_format": "message",
        "asr_options": {"enable_itn": True}
    }
}

response = requests.post(url, headers=headers, json=payload, timeout=120)
```

## LLM (Форматирование текста)

### Модель
- **Название:** `qwen-plus`
- **Fallback:** Gemini 2.5 Flash (`gemini-2.5-flash`)

### Endpoint
```
POST https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation
```

## Инфраструктура

### Function Compute
- Region: `eu-central-1`
- Runtime: `python3.10`
- Functions:
  - `telegram-whisper-bot-prod$webhook-handler`
  - `telegram-whisper-bot-prod$audio-processor`

### Environment Variables
- `DASHSCOPE_API_KEY` - API ключ DashScope
- `WHISPER_BACKEND=qwen-asr`
- `TELEGRAM_BOT_TOKEN`
- `TABLESTORE_ENDPOINT`
- `TABLESTORE_INSTANCE`
- `MNS_ENDPOINT`

### Layers
- `websocket-client` (для совместимости, но не используется в v3.0.0)

## Критические уроки

1. **REST API лучше WebSocket для serverless** - WebSocket `run_forever()` блокирует
2. **Используй чистый requests вместо dashscope SDK** - избегает проблем с зависимостями
3. **International endpoint:** всегда использовать `dashscope-intl.aliyuncs.com`
4. **Audio формат:** base64 data URI в multimodal conversation

## Структура проекта (v3.0.2)

### Удалено (очистка 2026-02-04)
- `telegram_bot_shared/` - дубликат `shared/`
- `audio-processor-deploy/telegram_bot_shared/` - дубликат
- `audio-processor-gpu/` - legacy GPU Whisper прототип
- `SYSTEM_ARCHITECTURE.md` - устарел

### Сохранено как reference
- `handlers/` - GCP команды для портирования в Alibaba

### Alibaba версия неполная!
Отсутствуют команды: `/buy_*`, `/user`, `/credit`, `/stat`, `/metrics`, `/review_trials`, `/export`, `/report`, `/flush`, `/status`

## Session Summary (2026-02-04)

**Аудит проекта:**
- Оценка: 6.9/10
- Файлов в git: 150 → 123 (-18%)
- Удалено ~7500 строк дубликатов

**Коммит:** `chore(v3.0.2): Project cleanup and documentation update`
