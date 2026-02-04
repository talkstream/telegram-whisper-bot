# UI/UX Improvements v2.0.0

## Performance Impact Analysis

### Bottlenecks Identified

| #  | Bottleneck | Impact | Mitigation |
|----|------------|--------|------------|
| 1  | **GPU Cold Start** | 30-60 сек (VM) + 5-7 сек (model) | Keep min_instances=1, или гибридный подход |
| 2  | **Spot VM Preemption** | 5-15% вероятность прерывания | Pub/Sub retry + graceful messaging |
| 3  | **GPU RTF 1.5-2.0x** | 1 мин аудио = 90-120 сек | Hybrid: короткое → API, длинное → GPU |
| 4  | **Telegram Download** | 1-5 сек на файл | Параллельная загрузка (не критично) |
| 5  | **Pub/Sub Latency** | 100-300ms | Приемлемо |

### Backend Selection Strategy

```python
def select_backend(audio_duration_sec: int, user: User) -> str:
    """
    Гибридный выбор backend для оптимального баланса
    скорости и стоимости.
    """
    if audio_duration_sec < 30:
        # Короткое аудио: latency критична
        return 'openai'  # ~10 сек total

    elif audio_duration_sec < 180:  # 3 минуты
        # Среднее: баланс
        if user.is_premium or user.prefers_speed:
            return 'openai'
        return 'gpu'  # Экономия 10-30×

    elif audio_duration_sec < 600:  # 10 минут
        # Длинное: GPU выгоднее
        return 'gpu'  # Экономия 30-50×

    else:
        # Очень длинное: только GPU
        return 'gpu'  # Экономия 50-100×
```

## New Progress UI

### Before (v1.x)
```
⏳ Распознаю речь...
Ожидаемое время: ~45 секунд
```

### After (v2.0.0)
```
🎙 Распознавание...

[▓▓▓▓▓▓░░░░] 60%

⏱ Осталось: ~25 сек.

🖥 GPU-обработка
```

### Visual Progress Stages

| Progress | Emoji | Stage |
|----------|-------|-------|
| 0-19%    | 🔄    | Starting |
| 20-34%   | 📥    | Downloading |
| 35-49%   | 🔧    | Converting |
| 50-79%   | 🎙    | Transcribing |
| 80-94%   | ✨    | Formatting |
| 95-99%   | 📤    | Sending |
| 100%     | ✅    | Done |

## Graceful Degradation Messages

### GPU Cold Start
```
🖥 Запускаю GPU-сервер...

Это может занять до 1 минуты при первом запуске.
Последующие запросы будут быстрее.
```

### Spot VM Preemption
```
⚠️ Обработка была прервана

Ваш запрос автоматически перезапущен.
Это может добавить 1-2 минуты к времени ожидания.
```

### Queue Position (if implemented)
```
📋 Ваш запрос в очереди

Позиция: 3
Ожидание: ~2 мин.
```

### Fallback to API
```
🔄 Переключаюсь на быструю обработку...

GPU-сервер занят, использую облачный API.
```

### Long Audio Warning
```
📢 Длинная запись (15 мин.)

Обработка займёт ~22 мин.
Вы получите результат, как только он будет готов.
```

## Implementation

### Using ProgressService

```python
from telegram_bot_shared.services.progress import (
    ProgressService,
    ProcessingStage,
    GracefulDegradationMessages
)

# Initialize
progress_service = ProgressService(telegram_service)

# Create state for job
state = progress_service.create_state(
    job_id=job_id,
    chat_id=chat_id,
    message_id=status_message_id,
    audio_duration=duration,
    backend="gpu"  # or "openai"
)

# Update progress
progress_service.update(state, ProcessingStage.DOWNLOADING)
progress_service.update(state, ProcessingStage.CONVERTING)
progress_service.update(state, ProcessingStage.TRANSCRIBING, sub_progress=0.5)
progress_service.update(state, ProcessingStage.FORMATTING)

# Complete
progress_service.complete(state)
```

### Graceful Degradation

```python
# On GPU cold start
if gpu_is_cold:
    telegram.send_message(chat_id, GracefulDegradationMessages.gpu_cold_start())

# On preemption detected
if preemption_detected:
    telegram.send_message(chat_id, GracefulDegradationMessages.preemption_recovery())

# On long audio
if duration > 600:  # 10 min
    telegram.send_message(
        chat_id,
        GracefulDegradationMessages.long_audio_warning(duration // 60)
    )
```

## Best Practices 2026 (from research)

### 1. Progress Indicators
- ✅ Show visual progress bar
- ✅ Display estimated time remaining
- ✅ Update at reasonable intervals (3-10 sec)
- ✅ Avoid flickering (debounce updates)

### 2. Multi-Step Processes
- ✅ Show current stage name
- ✅ Indicate overall progress
- ✅ Handle interruptions gracefully

### 3. Real-Time Feedback
- ✅ Telegram Reactions API (Bot API 8.0) for quick feedback
- ✅ Chat actions (typing, upload_document)
- ✅ Immediate acknowledgment on file receive

### 4. Session Persistence
- ⏳ Remember user's place if they leave
- ⏳ Allow resume of interrupted operations

### 5. Error Handling
- ✅ Clear, non-alarming error messages
- ✅ Actionable recommendations
- ✅ Fallback options when available

## Telegram API Rate Limits

- Message edits: ~30/minute per chat
- Our update interval: 3 sec minimum
- Max updates per minute: 20 (safe margin)

## Files Added/Modified

| File | Description |
|------|-------------|
| `shared/.../services/progress.py` | New ProgressService |
| `docs/UX_IMPROVEMENTS.md` | This documentation |
| `audio_processor.py` | To be updated to use ProgressService |

## Sources

- [10 Best UX Practices for Telegram Bots](https://medium.com/@bsideeffect/10-best-ux-practices-for-telegram-bots-79ffed24b6de)
- [Telegram Bot Development Guide 2025](https://wnexus.io/the-complete-guide-to-telegram-bot-development-in-2025/)
- [Chatbots Best Practices 2026](https://www.revechat.com/blog/chatbot-best-practices/)
- [Telegram Reactions API](https://wyu-telegram.com/blogs/1422367544/)
