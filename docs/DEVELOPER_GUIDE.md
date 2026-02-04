# Developer Guide

Руководство для разработчиков Telegram Whisper Bot.

## Архитектура

```
alibaba/
├── shared/                    # Единственный источник истины (v3.3.0+)
│   ├── audio.py              # AudioService: Qwen3-ASR + Qwen-turbo
│   ├── tablestore_service.py # TablestoreService: DB с optimistic locking
│   ├── telegram.py           # TelegramService: Telegram API
│   ├── mns_service.py        # MNSService + MNSPublisher
│   └── utility.py            # Утилиты
├── webhook-handler/           # FastAPI (FC trigger: HTTP)
│   ├── main.py               # Команды, callbacks (~1400 строк)
│   └── services/             # ← Автогенерация из shared/
└── audio-processor/           # Worker (FC trigger: MNS)
    ├── handler.py            # MNS message handler
    └── services/             # ← Автогенерация из shared/
```

### Поток данных

```
Telegram → Webhook Handler → MNS Queue → Audio Processor → Telegram
              │                              │
              └── Tablestore ←───────────────┘
```

1. **Webhook Handler** получает сообщение от Telegram
2. Для коротких аудио (<60 сек) — синхронная обработка
3. Для длинных аудио — задача в MNS очередь
4. **Audio Processor** обрабатывает задачу асинхронно
5. Результат отправляется в Telegram и сохраняется в Tablestore

## Технологии

| Компонент | Технология |
|-----------|------------|
| Runtime | Python 3.11 |
| Web Framework | FastAPI |
| Cloud | Alibaba Cloud Function Compute 3.0 |
| Database | Tablestore (NoSQL) |
| Queue | MNS (Message Service) |
| ASR | Qwen3-ASR-Flash (DashScope REST API) |
| LLM | Qwen-turbo (резерв: Gemini 2.5 Flash) |
| Deployment | Serverless Devs (s.yaml) |

## Локальная разработка

### Требования

- Python 3.11+
- FFmpeg (для обработки аудио)
- Alibaba Cloud CLI или Serverless Devs

### Установка

```bash
# Клонирование
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot

# Virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Зависимости
pip install -r alibaba/webhook-handler/requirements.txt
pip install -r alibaba/audio-processor/requirements.txt
```

### Переменные окружения

```bash
# Telegram
export TELEGRAM_BOT_TOKEN="your_bot_token"
export BOT_OWNER_ID="your_telegram_id"

# DashScope (ASR + LLM)
export DASHSCOPE_API_KEY="your_dashscope_key"
export WHISPER_BACKEND="qwen-asr"

# Tablestore
export TABLESTORE_ENDPOINT="https://your-instance.eu-central-1.ots.aliyuncs.com"
export TABLESTORE_INSTANCE="your_instance_name"
export ALIBABA_CLOUD_ACCESS_KEY_ID="your_key_id"
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="your_secret"

# MNS
export MNS_ENDPOINT="https://your-account.mns.eu-central-1.aliyuncs.com"
export MNS_QUEUE_NAME="audio-processing-queue"

# Gemini fallback (опционально)
export GOOGLE_API_KEY="your_google_api_key"
```

## Деплой

### Serverless Devs

```bash
# Установка
npm install -g @serverless-devs/s

# Деплой
cd alibaba
s deploy -y
```

### Структура s.yaml

```yaml
# s.yaml находится в .gitignore (содержит секреты)
edition: 3.0.0
name: twbot-p

vars:
  region: eu-central-1

services:
  webhook-handler:
    component: fc3
    actions:
      pre-deploy:
        - run: cp -r ../shared/* services/
    props:
      runtime: python3.11
      memorySize: 512
      timeout: 60

  audio-processor:
    component: fc3
    actions:
      pre-deploy:
        - run: cp -r ../shared/* services/
    props:
      runtime: python3.11
      memorySize: 1024
      timeout: 300
```

### Webhook настройка

```bash
# Установить webhook
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-fc-endpoint/webhook"}'

# Проверить webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

## База данных (Tablestore)

### Таблицы

| Таблица | Primary Key | Описание |
|---------|-------------|----------|
| users | user_id (string) | Пользователи, балансы, настройки |
| audio_jobs | job_id (string) | Задачи на обработку |
| trial_requests | user_id (string) | Запросы на trial |
| transcription_logs | log_id (string) | Логи транскрипций |
| payment_logs | payment_id (string) | Логи платежей |

### Паттерны работы

```python
# ВАЖНО: всегда lowercase 'put' в update_columns
from tablestore import Row, Condition, RowExistenceExpectation

primary_key = [('user_id', str(user_id))]
update_columns = {'put': [('balance_minutes', 100)]}  # lowercase!
row = Row(primary_key, update_columns)
condition = Condition(RowExistenceExpectation.EXPECT_EXIST)
client.update_row('users', row, condition)
```

## Сервисный слой

### AudioService

```python
from services.audio import AudioService

audio_service = AudioService(api_key=DASHSCOPE_API_KEY)

# Транскрипция
result = await audio_service.transcribe_audio(
    audio_data=bytes_data,
    file_format="mp3"
)

# Форматирование через LLM
formatted = await audio_service.format_with_llm(
    text=result.text,
    use_yo=True
)
```

### TablestoreService

```python
from services.tablestore_service import TablestoreService

db = TablestoreService(
    endpoint=TABLESTORE_ENDPOINT,
    instance=TABLESTORE_INSTANCE
)

# Получить пользователя
user = db.get_user(user_id)

# Списать баланс
db.deduct_balance(user_id, minutes=5)

# Создать задачу
db.create_job(job_id, user_id, status="pending")
```

### TelegramService

```python
from services.telegram import TelegramService

tg = TelegramService(token=BOT_TOKEN)

# Отправить сообщение
await tg.send_message(chat_id, text)

# Скачать файл
file_data = await tg.download_file(file_id)
```

## FFmpeg настройки

```bash
# Стандартная конвертация (>10 сек)
ffmpeg -y -i input.ogg -b:a 32k -ar 16000 -ac 1 -threads 4 output.mp3

# Короткие аудио (<10 сек)
ffmpeg -y -i input.ogg -b:a 24k -ar 8000 -ac 1 -threads 4 output.mp3
```

## Форк и кастомизация

### 1. Форкните репозиторий

```bash
gh repo fork talkstream/telegram-whisper-bot --clone
```

### 2. Создайте бота

- Откройте [@BotFather](https://t.me/BotFather)
- `/newbot` → выберите имя и username
- Сохраните токен

### 3. Настройте Alibaba Cloud

1. Создайте аккаунт на [aliyun.com](https://www.alibabacloud.com/)
2. Включите сервисы: Function Compute, Tablestore, MNS
3. Получите API ключи
4. Получите DashScope API key

### 4. Создайте s.yaml

```bash
cp alibaba/s.yaml.example alibaba/s.yaml
# Отредактируйте s.yaml с вашими значениями
```

### 5. Задеплойте

```bash
cd alibaba
s deploy -y
```

## Мониторинг

### Логи FC

```bash
# Webhook handler
s logs --tail webhook-handler

# Audio processor
s logs --tail audio-processor
```

### Метрики

Используйте админ-команды бота:
- `/status` — статус очереди MNS
- `/metrics 24` — метрики за 24 часа
- `/cost` — расходы на обработку

## Тестирование

```bash
# Unit tests
pytest tests/ -v

# Интеграционные тесты (требуют настроенные переменные)
pytest tests/integration/ -v --run-integration
```

## Частые проблемы

См. [TROUBLESHOOTING.md](TROUBLESHOOTING.md) для решения типичных проблем.

---

*Версия: v3.3.0*
