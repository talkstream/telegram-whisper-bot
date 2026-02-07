# Telegram Whisper Bot

**AI-бот для транскрипции голосовых сообщений в Telegram**

[![Version](https://img.shields.io/badge/version-3.6.0-blue.svg)](https://github.com/talkstream/telegram-whisper-bot)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-@editorialsrobot-blue?logo=telegram)](https://t.me/editorialsrobot)

Голосовые, аудио и видео → отформатированный текст. Qwen3-ASR + Qwen LLM.

**[Попробовать →](https://t.me/editorialsrobot)**

---

## Быстрый старт

1. Откройте [@editorialsrobot](https://t.me/editorialsrobot)
2. Отправьте голосовое или аудио/видео файл
3. Получите текст через несколько секунд

Пробный период: 15 минут бесплатно (`/trial`).

---

## Возможности

- **Транскрипция** — голосовые, аудио, видео (до 1 часа)
- **Диаризация** — определение спикеров в разговоре (`/dialogue`)
- **Форматирование** — пунктуация, абзацы, «ё» через LLM
- **Чанкинг** — автосплит длинных аудио (>2.5 мин)
- **52 языка** — автоопределение
- **Оплата в Stars** — Telegram-нативная

---

## Форматы

| Тип | Расширения |
|-----|------------|
| Аудио | `.ogg`, `.opus`, `.mp3`, `.wav`, `.aac`, `.m4a`, `.flac` |
| Видео | `.mp4`, `.mov`, `.webm`, `.mkv` |

Лимиты: 20 МБ, 1 час.

---

## Тарифы

Оплата через **Telegram Stars**.

| Пакет | Минуты | Цена | ⭐/мин |
|-------|--------|------|--------|
| Micro | 10 | 5 ⭐ | 0.50 |
| Start | 50 | 35 ⭐ | 0.70 |
| Standard | 200 | 119 ⭐ | 0.60 |
| Profi | 1000 | 549 ⭐ | 0.55 |
| MAX | 8888 | 4444 ⭐ | 0.50 |

> **Micro** — промо, макс. 3 покупки на аккаунт.

---

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Регистрация |
| `/help` | Справка |
| `/balance` | Баланс минут |
| `/trial` | Пробный период (15 мин) |
| `/buy_minutes` | Купить минуты |
| `/settings` | Настройки |
| `/code` | Моноширинный шрифт |
| `/yo` | Буква «ё» |
| `/output` | Режим длинного текста (сообщения / .txt файл) |
| `/dialogue` | Режим диалога (диаризация) |

---

## Технические характеристики

| Параметр | Значение |
|----------|----------|
| ASR | Qwen3-ASR-Flash (REST), Fun-ASR (диаризация) |
| LLM | Qwen-turbo (fallback: Gemini 2.5 Flash) |
| TTFT | ~92 мс |
| Синхронная обработка | до 60 сек |
| Асинхронная | 60+ сек (MNS) |
| Регион | EU (Франкфурт) |
| Cold start | 2-3 сек |

---

## Changelog

### [3.6.0] — 2026-02-07
- Диаризация (Fun-ASR) — определение спикеров
- `/output` — переключение файл/.txt vs сообщения
- `/dialogue` — режим диалога
- `/mute` — отключение уведомлений об ошибках
- `_build_format_prompt()` — DRY промпт для Qwen+Gemini
- Правила имён собственных и шипящих в LLM-промпте
- SLS логирование + TelegramErrorHandler
- `send_as_file()` — отправка .txt

### [3.5.0] — 2026-02-07
- ASR-чанкинг (>150с → автосплит)
- Обработка документов (file_name-based)
- Адаптивное сжатие аудио
- User-friendly ошибки

### [3.4.0] — 2026-02-06
- Evolving progress messages
- Typing indicators
- DB: 6→4 round-trips (-150 мс)
- LLM skip для текста ≤100 символов

Полная история: [docs/archive/VERSION_HISTORY.md](docs/archive/VERSION_HISTORY.md)

---

## Для разработчиков

| Документ | Описание |
|----------|----------|
| [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Архитектура, деплой, форк |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Админ-команды |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Проблемы и решения |
| [CLAUDE.md](CLAUDE.md) | AI-контекст |

```bash
cd alibaba && s deploy -y
```

---

## Лицензия

MIT — см. [LICENSE](LICENSE).

**Бот:** [@editorialsrobot](https://t.me/editorialsrobot) | **Repo:** [github.com/talkstream/telegram-whisper-bot](https://github.com/talkstream/telegram-whisper-bot)
