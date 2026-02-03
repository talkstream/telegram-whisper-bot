# План миграции Telegram Whisper Bot на Alibaba Cloud

**Версия:** v3.0.0
**Дата:** 2026-02-04
**Статус:** В работе

---

## 📊 Текущая архитектура (GCP + Alibaba ASR)

```
┌─────────────────────────────────────────────────────────────┐
│  Google Cloud Platform                                       │
│  ├─ App Engine (webhook handler, FastAPI/uvicorn)           │
│  ├─ Cloud Functions (audio processor, Pub/Sub trigger)      │
│  ├─ Firestore (users, jobs, logs, payments)                 │
│  ├─ Pub/Sub (audio-processing-jobs topic)                   │
│  ├─ Secret Manager (tokens, API keys)                       │
│  └─ Cloud Scheduler (cron jobs for reports)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ API calls
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Alibaba Cloud (ASR only)                                    │
│  ├─ DashScope API (Paraformer transcription)                │
│  └─ OSS (temporary file storage)                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Целевая архитектура (100% Alibaba Cloud)

```
┌─────────────────────────────────────────────────────────────┐
│  Alibaba Cloud                                               │
│  ├─ SAE / Function Compute (webhook handler)                │
│  ├─ Function Compute (audio processor)                      │
│  ├─ Tablestore / Lindorm (users, jobs, logs, payments)      │
│  ├─ MNS / EventBridge (message queue)                       │
│  ├─ KMS (secrets and API keys)                              │
│  ├─ API Gateway (HTTPS endpoint for Telegram)               │
│  ├─ DashScope (Paraformer ASR + Qwen LLM formatting)       │
│  └─ OSS (file storage)                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔄 Компонентное сопоставление

| GCP Компонент | Alibaba Cloud Компонент | Примечания |
|---------------|------------------------|------------|
| App Engine | **SAE (Serverless App Engine)** | Python/FastAPI container |
| Cloud Functions | **Function Compute 3.0** | Python 3.12, event-driven |
| Firestore | **Tablestore** | Wide-column NoSQL |
| Pub/Sub | **MNS (Simple Message Queue)** | Message queue service |
| Secret Manager | **KMS** | Key Management Service |
| Cloud Scheduler | **Cloud Scheduler** или Function Compute triggers | Cron jobs |
| Cloud Logging | **SLS (Log Service)** | Logging and monitoring |

---

## 📋 План миграции по фазам

### Фаза 1: Подготовка инфраструктуры (День 1)

#### 1.1 Создание Alibaba Cloud ресурсов
```bash
# Через Terraform или консоль
- [ ] Создать VPC в регионе eu-central-1 (Frankfurt)
- [ ] Настроить Security Groups
- [ ] Создать Tablestore instance
- [ ] Настроить MNS queue
- [ ] Создать API Gateway endpoint
- [ ] Настроить KMS для секретов
```

#### 1.2 Миграция секретов в KMS
```python
# Секреты для переноса:
secrets = {
    'telegram-bot-token': '...',
    'alibaba-api-key': '...',          # уже есть
    'alibaba-oss-bucket': '...',       # уже есть
    'alibaba-oss-endpoint': '...',     # уже есть
    'alibaba-access-key-id': '...',    # уже есть
    'alibaba-access-key-secret': '...' # уже есть
}
```

### Фаза 2: Миграция базы данных (День 1-2)

#### 2.1 Создать схему Tablestore
```python
# Таблицы для создания:
tables = [
    'users',           # user_id (PK), balance, settings, created_at
    'audio_jobs',      # job_id (PK), user_id, status, created_at
    'trial_requests',  # user_id (PK), status, request_timestamp
    'transcription_logs',  # log_id (PK), user_id, timestamp, duration
    'payment_logs',    # payment_id (PK), user_id, amount, timestamp
]
```

#### 2.2 Миграция данных из Firestore
```python
# Скрипт миграции:
# 1. Export из Firestore в JSON
# 2. Transform для Tablestore schema
# 3. Import в Tablestore
```

### Фаза 3: Адаптация сервисов (День 2-3)

#### 3.1 Создать TablestoreService
```python
# shared/telegram_bot_shared/services/tablestore_service.py
from tablestore import OTSClient

class TablestoreService:
    def __init__(self, endpoint, access_key_id, access_key_secret, instance_name):
        self.client = OTSClient(endpoint, access_key_id, access_key_secret, instance_name)

    def get_user(self, user_id: int) -> dict:
        """Get user by ID from Tablestore"""
        pass

    def update_user_balance(self, user_id: int, delta: int):
        """Update user balance atomically"""
        pass

    # ... остальные методы аналогично FirestoreService
```

#### 3.2 Создать MNSService
```python
# shared/telegram_bot_shared/services/mns_service.py
from mns.queue import Queue

class MNSService:
    def __init__(self, endpoint, access_key_id, access_key_secret, queue_name):
        self.queue = Queue(endpoint, queue_name)

    def publish_job(self, job_data: dict):
        """Publish audio processing job to MNS queue"""
        pass

    def receive_job(self) -> dict:
        """Receive and process job from MNS queue"""
        pass
```

### Фаза 4: Деплой Function Compute (День 3)

#### 4.1 Webhook Handler (SAE или FC)
```yaml
# s.yaml (Serverless Devs конфиг)
edition: 3.0.0
name: telegram-whisper-bot
access: aliyun

vars:
  region: eu-central-1
  service:
    name: telegram-whisper-bot
    description: Telegram Whisper Bot Webhook Handler

services:
  webhook-handler:
    component: fc3
    props:
      region: ${vars.region}
      functionName: webhook-handler
      runtime: python3.12
      handler: main.handler
      memorySize: 512
      timeout: 60
      triggers:
        - name: http-trigger
          type: http
          config:
            authType: anonymous
            methods:
              - POST
              - GET
```

#### 4.2 Audio Processor (FC)
```yaml
services:
  audio-processor:
    component: fc3
    props:
      region: ${vars.region}
      functionName: audio-processor
      runtime: python3.12
      handler: audio_processor.handler
      memorySize: 1024
      timeout: 540
      triggers:
        - name: mns-trigger
          type: mns_topic
          config:
            topicName: audio-processing-jobs
```

### Фаза 5: Настройка API Gateway (День 3)

#### 5.1 Создать HTTPS endpoint
```bash
# API Gateway конфигурация
- Endpoint: https://telegram-bot.eu-central-1.alibabacloud.com
- Backend: Function Compute webhook-handler
- SSL: Managed certificate
```

#### 5.2 Обновить Telegram webhook
```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://telegram-bot.eu-central-1.alibabacloud.com/"}'
```

### Фаза 6: Тестирование (День 4)

#### 6.1 Unit тесты
- [ ] TablestoreService CRUD операции
- [ ] MNSService publish/receive
- [ ] AudioService с Paraformer

#### 6.2 Integration тесты
- [ ] Полный цикл: Telegram → FC → MNS → FC → Telegram
- [ ] Billing и balance update
- [ ] Trial request flow

#### 6.3 Load тесты
- [ ] 10 concurrent audio files
- [ ] Measure latency vs GCP baseline

### Фаза 7: Cutover (День 5)

#### 7.1 Финальная миграция данных
```bash
# Sync последних изменений из Firestore
python scripts/sync_firestore_to_tablestore.py --since="2026-02-04"
```

#### 7.2 Переключение webhook
```bash
# Обновить webhook на Alibaba Cloud endpoint
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d '{"url": "https://telegram-bot.eu-central-1.fc.aliyuncs.com/webhook"}'
```

#### 7.3 Мониторинг
- Следить за логами в SLS
- Проверять latency и error rate
- Готовность к rollback

---

## 📁 Новая структура проекта

```
telegram-whisper-bot/
├── alibaba/                          # Alibaba Cloud specific
│   ├── s.yaml                        # Serverless Devs config
│   ├── webhook-handler/              # SAE/FC webhook handler
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── audio-processor/              # FC audio processor
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── terraform/                    # Infrastructure as code
│       ├── main.tf
│       ├── tablestore.tf
│       ├── mns.tf
│       └── kms.tf
├── shared/
│   └── telegram_bot_shared/
│       └── services/
│           ├── tablestore_service.py # NEW: Tablestore adapter
│           ├── mns_service.py        # NEW: MNS adapter
│           ├── audio.py              # Updated for Alibaba
│           └── ...
├── gcp/                              # Deprecated GCP code (archive)
│   ├── app.yaml
│   └── audio-processor-deploy/
└── docs/
    └── ALIBABA_MIGRATION_PLAN.md     # This file
```

---

## 💰 Ожидаемая экономия

| Компонент | GCP (текущий) | Alibaba Cloud | Экономия |
|-----------|---------------|---------------|----------|
| Compute | ~$15/мес | ~$5/мес | -67% |
| Database | ~$3/мес | ~$1/мес | -67% |
| Queue | ~$1/мес | ~$0/мес | -100% |
| ASR | ~$6/мес | ~$2/мес | -67% |
| **Итого** | **~$25/мес** | **~$8/мес** | **-68%** |

---

## ⚠️ Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Tablestore API отличается от Firestore | Высокая | Adapter pattern, тесты |
| MNS latency выше Pub/Sub | Средняя | Benchmark, оптимизация |
| Документация на китайском | Средняя | English docs, сообщество |
| Regional availability | Низкая | EU region (Frankfurt) |
| Rollback сложность | Средняя | Сохранить GCP infra 2 недели |

---

## 🔧 Требуемые зависимости

```txt
# requirements.txt additions
tablestore>=5.4.0
aliyun-mns>=1.1.6
alibabacloud-kms20160120>=2.0.0
alibabacloud-tea-openapi>=0.3.0
```

---

## ✅ Чеклист миграции

### Подготовка
- [ ] Alibaba Cloud аккаунт активирован
- [ ] RAM user с необходимыми правами
- [ ] VPC и Security Groups созданы
- [ ] Terraform state backend настроен

### Фаза 1: Инфраструктура
- [ ] Tablestore instance создан
- [ ] MNS queue создан
- [ ] KMS secrets настроены
- [ ] API Gateway endpoint создан

### Фаза 2: База данных
- [ ] Tablestore schema создана
- [ ] Данные мигрированы из Firestore
- [ ] Проверена консистентность данных

### Фаза 3: Сервисы
- [ ] TablestoreService реализован и протестирован
- [ ] MNSService реализован и протестирован
- [ ] AudioService адаптирован

### Фаза 4: Деплой
- [ ] Webhook handler задеплоен
- [ ] Audio processor задеплоен
- [ ] Triggers настроены

### Фаза 5: Тестирование
- [ ] Unit тесты пройдены
- [ ] Integration тесты пройдены
- [ ] Load тесты выполнены

### Фаза 6: Cutover
- [ ] Финальная sync данных
- [ ] Webhook переключен
- [ ] Мониторинг активен
- [ ] Rollback план готов

### Фаза 7: Cleanup
- [ ] GCP ресурсы остановлены (после 2 недель)
- [ ] Billing alerts настроены
- [ ] Документация обновлена

---

## 📚 Источники

- [Alibaba Cloud SAE Documentation](https://www.alibabacloud.com/help/en/sae/)
- [Function Compute 3.0](https://www.alibabacloud.com/help/en/functioncompute/fc-3-0/)
- [Tablestore Developer Guide](https://www.alibabacloud.com/help/en/tablestore/)
- [MNS Documentation](https://www.alibabacloud.com/help/en/mns/)
- [Serverless Devs](https://www.serverless-devs.com/)
- [Migration Best Practices](https://www.alibabacloud.com/solutions/cloud-migration)
