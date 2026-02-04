# Admin Guide

Админ-команды доступны только владельцу бота (`BOT_OWNER_ID`).

## Команды

### Пользователи

| Команда | Описание |
|---------|----------|
| `/user [search] [--page N]` | Поиск по username/имени/ID |
| `/credit <id> <min>` | Добавить/вычесть минуты |

**Примеры:**
```
/user john --page 2    # Поиск "john", страница 2
/credit 123456789 30   # +30 минут
/credit 123456789 -10  # -10 минут
```

### Trial

| Команда | Описание |
|---------|----------|
| `/review_trials` | Ожидающие запросы (inline кнопки ✅/❌) |

Одобрение = 15 минут + уведомление пользователю.

### Статистика

| Команда | Описание |
|---------|----------|
| `/stat` | Пользователи, транскрипции, длительность |
| `/cost` | Расходы ASR/LLM за период |
| `/metrics [hours]` | Производительность (TTFT, успешность, языки) |

### Очередь

| Команда | Описание |
|---------|----------|
| `/status` | Pending/processing/completed/failed |
| `/flush` | Очистка зависших (>10 мин processing) |
| `/batch [user_id]` | Задачи пользователя |

### Экспорт

| Команда | Описание |
|---------|----------|
| `/export users 30` | CSV пользователей за 30 дней |
| `/export logs 7` | CSV транскрипций за 7 дней |
| `/export payments 90` | CSV платежей за 90 дней |

### Отчёты

| Команда | Описание |
|---------|----------|
| `/report daily` | Дневной: новые, транскрипции, доходы, топ |
| `/report weekly` | Недельный |

## Автоматические уведомления

| Событие | Получатель |
|---------|------------|
| Баланс < 5 мин | Пользователь |
| Новый trial запрос | Админ |
| >3 failures подряд | Админ |
| Queue > 100 pending | Админ |

## Мониторинг

```bash
s logs --tail webhook-handler
s logs --tail audio-processor
```

**Alibaba Console:** FC Metrics, Tablestore Monitoring, MNS Statistics

| Метрика | Норма | Алерт |
|---------|-------|-------|
| Cold start | < 3s | > 5s |
| Processing | < 30s/min | > 60s/min |
| Success rate | > 95% | < 90% |
| Queue depth | < 10 | > 50 |

## Типичные операции

**Компенсация за баг:**
```
/user <username> → /credit <id> <min>
```

**Проверка проблемы:**
```
/user <username> → /batch <id> → /export logs 1
```

**Зависшая очередь:**
```
/status → /flush → /status
```

**Экстренное отключение:**
```bash
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook"
# После исправления:
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d '{"url": "https://endpoint/webhook"}'
```

## Безопасность

- Не `/credit` без причины
- Не `/flush` без `/status`
- Не экспорт на незащищённые устройства
- Все действия логируются (user_id, action, params, timestamp)

---

*v3.3.0*
