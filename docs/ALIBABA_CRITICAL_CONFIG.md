# Критическая конфигурация Alibaba Cloud для Telegram Whisper Bot

**ВНИМАНИЕ: НЕ ТЕРЯТЬ ЭТУ ИНФОРМАЦИЮ!**

**Последнее обновление:** 2026-02-04
**Версия:** v3.0.0

---

## 🎯 ASR модель (распознавание речи)

### Современная модель (ИСПОЛЬЗОВАТЬ!)

| Параметр | Значение |
|----------|----------|
| **Модель** | `qwen3-asr-flash` |
| **Snapshot** | `qwen3-asr-flash-2025-09-08` |
| **Языки** | 52 языка включая русский |
| **Протокол** | REST API (НЕ WebSocket!) |

### REST API endpoint (международный)

```
https://dashscope-intl.aliyuncs.com/api/v1
```

### Python SDK вызов

```python
import dashscope

dashscope.base_http_api_url = 'https://dashscope-intl.aliyuncs.com/api/v1'

response = dashscope.MultiModalConversation.call(
    api_key=api_key,
    model="qwen3-asr-flash",
    messages=[
        {"role": "system", "content": [{"text": ""}]},
        {"role": "user", "content": [{"audio": "data:audio/mpeg;base64,..."}]}
    ],
    result_format="message",
    asr_options={"enable_itn": True}
)
```

### Альтернатива: Realtime WebSocket (для стриминга)

| Параметр | Значение |
|----------|----------|
| **Модель** | `qwen3-asr-flash-realtime` |
| **Endpoint** | `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime` |
| **Headers** | `Authorization: Bearer <KEY>`, `OpenAI-Beta: realtime=v1` |

**Примечание:** WebSocket API сложнее в serverless окружении. REST API проще и надёжнее.

### Документация

- [Real-time speech recognition](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [Interaction flow](https://www.alibabacloud.com/help/en/model-studio/qwen-asr-realtime-interaction-process)
- [GitHub Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)

### ❌ НЕ использовать устаревшие модели

- ~~paraformer-realtime-v2~~ (устарела, 2024)
- ~~paraformer-v1~~ (устарела)
- ~~speech-to-text~~ (старый API)

---

## 🤖 LLM модель (форматирование текста)

### Основная модель

| Параметр | Значение |
|----------|----------|
| **Модель** | `qwen-plus` |
| **Endpoint** | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| **Fallback** | Gemini 2.0 Flash |

### REST API формат

```json
{
    "model": "qwen-plus",
    "input": {
        "messages": [{"role": "user", "content": "..."}]
    },
    "parameters": {}
}
```

---

## 🔑 Environment Variables

| Переменная | Описание | Обязательная |
|------------|----------|--------------|
| `DASHSCOPE_API_KEY` | API ключ DashScope | ✅ Да |
| `WHISPER_BACKEND` | Backend ASR (`qwen-asr`) | ✅ Да |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | ✅ Да |
| `TABLESTORE_ENDPOINT` | Endpoint Tablestore | ✅ Да |
| `TABLESTORE_INSTANCE` | Имя instance | ✅ Да |
| `MNS_ENDPOINT` | Endpoint MNS | ✅ Да |
| `GOOGLE_API_KEY` | API ключ Gemini (fallback) | Опционально |

---

## 📦 Python зависимости

### requirements.txt

```
# Alibaba Cloud SDK
tablestore>=6.3.0
aliyun-mns>=1.1.5
dashscope>=1.20.0
oss2>=2.18.0

# Telegram
python-telegram-bot>=20.0

# Audio processing
openai>=1.0.0
pydub>=0.25.0

# Utilities
pytz>=2024.1
httpx>=0.25.0
requests>=2.31.0
python-json-logger>=2.0.0
websocket-client>=1.6.0
```

### FC Layer для websocket-client

```
acs:fc:eu-central-1:5907469887573677:layers/websocket-client/versions/1
```

---

## 🌐 Endpoints

### International (Singapore region)

| Сервис | Endpoint |
|--------|----------|
| **DashScope ASR WebSocket** | `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime` |
| **DashScope LLM REST** | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| **Tablestore** | `https://twbot-prod.eu-central-1.ots.aliyuncs.com` |
| **MNS** | `https://5907469887573677.mns.eu-central-1.aliyuncs.com` |

### Beijing region (НЕ ИСПОЛЬЗОВАТЬ для международных пользователей)

- `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
- `https://dashscope.aliyuncs.com/...`

---

## 📡 WebSocket сессия ASR

### 1. session.update (начало сессии)

```json
{
    "event_id": "event_1",
    "type": "session.update",
    "session": {
        "modalities": ["text"],
        "input_audio_format": "pcm",
        "sample_rate": 16000,
        "input_audio_transcription": {
            "language": "ru"
        },
        "turn_detection": null
    }
}
```

### 2. input_audio_buffer.append (отправка аудио)

```json
{
    "event_id": "event_2",
    "type": "input_audio_buffer.append",
    "audio": "<base64_encoded_pcm_chunk>"
}
```

### 3. input_audio_buffer.commit (завершение аудио)

```json
{
    "event_id": "event_3",
    "type": "input_audio_buffer.commit"
}
```

### 4. session.finish (закрытие сессии)

```json
{
    "event_id": "event_4",
    "type": "session.finish"
}
```

### Формат аудио

- **Sample rate:** 16000 Hz
- **Channels:** 1 (mono)
- **Format:** PCM 16-bit little-endian (s16le)
- **Chunk size:** 3200 bytes (~100ms)

---

## 🔧 FFmpeg команды

### Конвертация в PCM для ASR

```bash
ffmpeg -y -i input.mp3 -ar 16000 -ac 1 -f s16le -acodec pcm_s16le output.wav
```

### Конвертация в MP3

```bash
ffmpeg -y -i input.ogg -b:a 64k -ar 16000 -ac 1 -threads 4 output.mp3
```

---

## 📊 Мониторинг

### Логи FC

```bash
# Alibaba Cloud CLI
aliyun fc-open ListFunctionLogs --ServiceName telegram-whisper-bot --FunctionName webhook-handler
```

### Метрики для отслеживания

- `qwen3-asr` - время транскрипции
- `qwen-llm` - время форматирования
- `gemini` - fallback форматирования

---

## 🚨 Troubleshooting

### "Model not found"

**Причина:** Используется устаревшая модель (paraformer-v2) вместо современной (qwen3-asr-flash-realtime)

**Решение:** Обновить модель в коде на `qwen3-asr-flash-realtime`

### "Connection timeout"

**Причина:** Неправильный endpoint (dashscope.aliyuncs.com вместо dashscope-intl.aliyuncs.com)

**Решение:** Использовать международный endpoint `-intl`

### "400 Bad Request"

**Причина:** Устаревший REST API формат вместо WebSocket

**Решение:** Использовать WebSocket API для ASR

---

## 📚 Источники

- [Qwen3-ASR-Flash-Realtime Documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [WebSocket Interaction Flow](https://www.alibabacloud.com/help/en/model-studio/qwen-asr-realtime-interaction-process)
- [DashScope API Reference](https://www.alibabacloud.com/help/en/model-studio/qwen-api-reference/)
- [Qwen3-ASR GitHub](https://github.com/QwenLM/Qwen3-ASR)
