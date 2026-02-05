# Developer Guide

Руководство для разработчиков Telegram Whisper Bot.

**Архитектура и конфигурация:** см. [CLAUDE.md](../CLAUDE.md)

## Технологии

| Компонент | Технология |
|-----------|------------|
| Runtime | Python 3.11 |
| Web Framework | FastAPI |
| Cloud | Alibaba Cloud FC 3.0 |
| Database | Tablestore (NoSQL) |
| Queue | MNS |
| ASR | Qwen3-ASR-Flash |
| LLM | Qwen-turbo (fallback: Gemini 2.5 Flash) |
| Deployment | Serverless Devs |

## Локальная разработка

### Установка

```bash
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot

python3.11 -m venv venv && source venv/bin/activate

pip install -r alibaba/webhook-handler/requirements.txt
pip install -r alibaba/audio-processor/requirements.txt
```

### Переменные окружения

```bash
# Telegram
export TELEGRAM_BOT_TOKEN="..."
export BOT_OWNER_ID="..."

# DashScope
export DASHSCOPE_API_KEY="..."
export WHISPER_BACKEND="qwen-asr"

# Tablestore
export TABLESTORE_ENDPOINT="https://instance.eu-central-1.ots.aliyuncs.com"
export TABLESTORE_INSTANCE="..."
export ALIBABA_CLOUD_ACCESS_KEY_ID="..."
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="..."

# MNS
export MNS_ENDPOINT="https://account.mns.eu-central-1.aliyuncs.com"
export MNS_QUEUE_NAME="audio-processing-queue"

# Gemini fallback (optional)
export GOOGLE_API_KEY="..."
```

## Деплой

```bash
npm install -g @serverless-devs/s
cd alibaba && s deploy -y
```

### Webhook

```bash
# Set webhook
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://fc-endpoint/webhook"}'

# Check webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

## База данных (Tablestore)

| Таблица | Primary Key | Назначение |
|---------|-------------|------------|
| users | user_id (str) | Пользователи, балансы |
| audio_jobs | job_id (str) | Задачи обработки |
| trial_requests | user_id (str) | Trial запросы |
| transcription_logs | log_id (str) | Логи транскрипций |
| payment_logs | payment_id (str) | Логи платежей |

**Паттерны работы:** см. [TROUBLESHOOTING.md](TROUBLESHOOTING.md#ошибки-tablestore)

## Форк и кастомизация

1. **Форк:** `gh repo fork talkstream/telegram-whisper-bot --clone`
2. **Бот:** [@BotFather](https://t.me/BotFather) → `/newbot` → сохранить токен
3. **Alibaba Cloud:** [aliyun.com](https://www.alibabacloud.com/) → FC, Tablestore, MNS, DashScope
4. **Конфигурация:** `cp alibaba/s.yaml.example alibaba/s.yaml` → редактировать
5. **Деплой:** `cd alibaba && s deploy -y`

## Мониторинг

```bash
s logs --tail webhook-handler
s logs --tail audio-processor
```

**Админ-команды:** `/status`, `/metrics 24`, `/cost`

## Тестирование

```bash
pytest tests/ -v
pytest tests/integration/ -v --run-integration
```

## Частые проблемы

См. [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

*v3.4.0*
