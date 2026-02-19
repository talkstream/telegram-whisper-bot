"""
Telegram Webhook Handler for Alibaba Cloud Function Compute
Full implementation with Tablestore, MNS, and Qwen-ASR
v3.1.0 - Complete admin commands and callback handlers
"""
import json
import logging
import os
import sys
import math
import csv
import tempfile
import time
from collections import defaultdict
from typing import Any, Dict, Optional, List

# Add shared services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'telegram_bot_shared'))

# Configure structured JSON logging for SLS
from services.utility import UtilityService
UtilityService.setup_logging(
    'webhook-handler',
    bot_token=os.environ.get('TELEGRAM_BOT_TOKEN'),
    owner_id=os.environ.get('OWNER_ID'),
)
logger = logging.getLogger(__name__)

# Environment variables
TABLESTORE_ENDPOINT = os.environ.get('TABLESTORE_ENDPOINT', 'https://twbot-prod.eu-central-1.ots.aliyuncs.com')
TABLESTORE_INSTANCE = os.environ.get('TABLESTORE_INSTANCE', 'twbot-prod')
MNS_ENDPOINT = os.environ.get('MNS_ENDPOINT')
AUDIO_JOBS_QUEUE = os.environ.get('AUDIO_JOBS_QUEUE', 'telegram-whisper-bot-prod-audio-jobs')
REGION = os.environ.get('REGION', 'eu-central-1')

# Credentials - FC provides these automatically via STS
# Try multiple possible env var names
ALIBABA_ACCESS_KEY = (
    os.environ.get('ALIBABA_ACCESS_KEY') or
    os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID') or
    os.environ.get('accessKeyID')
)
ALIBABA_SECRET_KEY = (
    os.environ.get('ALIBABA_SECRET_KEY') or
    os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET') or
    os.environ.get('accessKeySecret')
)
ALIBABA_SECURITY_TOKEN = (
    os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN') or
    os.environ.get('securityToken')
)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

# Sync processing threshold (seconds)
# Short audio (<15s): sync for immediate response
# Longer audio (>=15s): async for parallel processing + diarization for >=60s
SYNC_PROCESSING_THRESHOLD = 15

# Rate limiting: max requests per user in a sliding window
_RATE_LIMIT_MAX = 10  # max requests
_RATE_LIMIT_WINDOW = 1.0  # seconds
_rate_limits: Dict[int, list] = defaultdict(list)


def _is_rate_limited(user_id: int) -> bool:
    """Check if user exceeded rate limit (10 req/sec). Cleans up stale entries."""
    now = time.monotonic()
    timestamps = _rate_limits[user_id]

    # Remove expired timestamps
    cutoff = now - _RATE_LIMIT_WINDOW
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)

    if len(timestamps) >= _RATE_LIMIT_MAX:
        return True

    timestamps.append(now)
    return False

# Owner ID for admin commands
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))

# Log MNS availability at startup
if not MNS_ENDPOINT:
    logger.warning("MNS_ENDPOINT not configured — async processing disabled, sync fallback will be used")

# Trial minutes
TRIAL_MINUTES = 15

# Product packages for purchase
PRODUCT_PACKAGES = {
    "micro_10": {
        "title": "Промо-пакет 'Микро'",
        "description": "10 минут транскрибации",
        "payload": "buy_micro_10",
        "stars_amount": 5,
        "minutes": 10,
        "purchase_limit": 3
    },
    "start_50": {
        "title": "Пакет 'Старт'",
        "description": "50 минут транскрибации",
        "payload": "buy_start_50",
        "stars_amount": 35,
        "minutes": 50
    },
    "standard_200": {
        "title": "Пакет 'Стандарт'",
        "description": "200 минут транскрибации",
        "payload": "buy_standard_200",
        "stars_amount": 119,
        "minutes": 200
    },
    "profi_1000": {
        "title": "Пакет 'Профи'",
        "description": "1000 минут транскрибации",
        "payload": "buy_profi_1000",
        "stars_amount": 549,
        "minutes": 1000
    },
    "editorial_3000": {
        "title": "Пакет 'Редакция'",
        "description": "3000 минут транскрибации для редакций",
        "payload": "buy_editorial_3000",
        "stars_amount": 1399,
        "minutes": 3000
    },
    "max_8888": {
        "title": "Пакет 'MAX'",
        "description": "8888 минут транскрибации",
        "payload": "buy_max_8888",
        "stars_amount": 4444,
        "minutes": 8888
    }
}

# Global service instances (lazy initialization)
_db_service = None
_telegram_service = None


def get_db_service(access_key_id=None, access_key_secret=None, security_token=None):
    """Get or create Tablestore service instance."""
    global _db_service
    if _db_service is None:
        from services.tablestore_service import TablestoreService

        # Read env vars at runtime (not at module import time)
        ak_id = access_key_id or os.environ.get('ALIBABA_ACCESS_KEY') or ALIBABA_ACCESS_KEY
        ak_secret = access_key_secret or os.environ.get('ALIBABA_SECRET_KEY') or ALIBABA_SECRET_KEY
        sec_token = security_token or os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN') or ALIBABA_SECURITY_TOKEN
        ts_endpoint = os.environ.get('TABLESTORE_ENDPOINT') or TABLESTORE_ENDPOINT
        ts_instance = os.environ.get('TABLESTORE_INSTANCE') or TABLESTORE_INSTANCE

        logger.info(f"Creating TablestoreService: endpoint={ts_endpoint}, instance={ts_instance}, ak_len={len(ak_id) if ak_id else 0}")

        _db_service = TablestoreService(
            endpoint=ts_endpoint,
            access_key_id=ak_id,
            access_key_secret=ak_secret,
            security_token=sec_token,
            instance_name=ts_instance
        )
    return _db_service


def get_telegram_service():
    """Get or create Telegram service instance."""
    global _telegram_service
    if _telegram_service is None:
        from services.telegram import TelegramService
        _telegram_service = TelegramService(TELEGRAM_BOT_TOKEN)
    return _telegram_service


def create_http_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create HTTP response for FC3 HTTP Trigger.
    Returns dict with statusCode, headers, and body.
    """
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, ensure_ascii=False)
    }


def handler(event, context):
    """
    FC3 HTTP trigger handler for Telegram webhook.
    Uses event-based format (not WSGI).

    Args:
        event: Can be bytes (raw body) or dict (parsed JSON)
        context: FC context object with credentials
    """
    try:
        global ALIBABA_ACCESS_KEY, ALIBABA_SECRET_KEY, ALIBABA_SECURITY_TOKEN, _db_service, _telegram_service

        # Always reset services to ensure fresh credentials and tokens
        _db_service = None
        _telegram_service = None

        # Extract credentials from context if available
        if hasattr(context, 'credentials'):
            creds = context.credentials
            ALIBABA_ACCESS_KEY = getattr(creds, 'access_key_id', ALIBABA_ACCESS_KEY)
            ALIBABA_SECRET_KEY = getattr(creds, 'access_key_secret', ALIBABA_SECRET_KEY)
            ALIBABA_SECURITY_TOKEN = getattr(creds, 'security_token', ALIBABA_SECURITY_TOKEN)

        # Parse event - can be bytes or dict
        request_body = {}
        http_method = 'POST'

        if isinstance(event, bytes):
            # Raw bytes - try to parse as JSON
            try:
                event_str = event.decode('utf-8')
                event = json.loads(event_str)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning(f"Could not parse event bytes")
                event = {}

        if isinstance(event, dict):
            # FC3 HTTP trigger event format
            http_method = event.get('requestContext', {}).get('http', {}).get('method', 'POST')

            # Get body from event
            body = event.get('body', '')
            if event.get('isBase64Encoded', False):
                import base64
                body = base64.b64decode(body).decode('utf-8')

            if body:
                try:
                    request_body = json.loads(body) if isinstance(body, str) else body
                except json.JSONDecodeError:
                    pass

        # Extract request path for API routing
        http_path = event.get('requestContext', {}).get('http', {}).get('path', '/') if isinstance(event, dict) else '/'
        logger.info(f"HTTP method: {http_method}, path: {http_path}")

        # Handle GET requests
        if http_method.upper() == 'GET':
            # Mini App HTML page
            if http_path == '/upload' or http_path.endswith('/upload'):
                return _serve_upload_page()

            # Health check (default GET)
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'status': 'ok',
                    'service': 'telegram-whisper-bot',
                    'region': REGION,
                    'version': '5.0.0',
                    'telegram_token_set': bool(TELEGRAM_BOT_TOKEN),
                    'telegram_token_len': len(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else 0
                }, ensure_ascii=False)
            }

        # API endpoints for Mini App
        if http_path.endswith('/api/signed-url'):
            return _handle_signed_url_request(request_body, event)
        if http_path.endswith('/api/process'):
            return _handle_process_upload(request_body, event)

        logger.info(f"Processing update: {str(request_body)[:200]}")

        result = process_update(request_body)

        return create_http_response(200, {'ok': True, 'result': result})

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return create_http_response(500, {'ok': False, 'error': str(e)})


def process_update(update: Dict[str, Any]) -> str:
    """Process a Telegram update."""
    import uuid
    from services.utility import set_trace_context

    # Generate trace_id for this request and set logging context
    trace_id = str(uuid.uuid4())[:8]
    user_id = (
        update.get('message', {}).get('from', {}).get('id')
        or update.get('callback_query', {}).get('from', {}).get('id')
        or ''
    )
    set_trace_context(trace_id=trace_id, user_id=user_id)

    # Rate limiting (skip for owner to avoid locking out admin)
    if user_id and isinstance(user_id, int) and user_id != OWNER_ID:
        if _is_rate_limited(user_id):
            logger.warning(f"Rate limited user {user_id}")
            return 'rate_limited'

    # Handle callback query
    if 'callback_query' in update:
        return handle_callback_query(update['callback_query'])

    # Handle pre_checkout_query (payments)
    if 'pre_checkout_query' in update:
        return handle_pre_checkout(update['pre_checkout_query'])

    # Handle message (check for successful_payment first)
    if 'message' in update:
        message = update['message']
        # Handle successful_payment inside message
        if 'successful_payment' in message:
            return handle_successful_payment(message)
        return handle_message(message)

    return 'no_action'


def handle_message(message: Dict[str, Any]) -> str:
    """Handle incoming message."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    text = message.get('text', '')

    # Ensure user exists
    db = get_db_service()
    user = db.get_user(user_id)
    if not user:
        # Create new user with auto-trial
        user_data = {
            'first_name': message.get('from', {}).get('first_name', ''),
            'last_name': message.get('from', {}).get('last_name', ''),
            'username': message.get('from', {}).get('username', ''),
            'balance_minutes': TRIAL_MINUTES,
            'trial_status': 'approved',
            'settings': json.dumps({'use_code_tags': False, 'use_yo': True})
        }
        db.create_user(user_id, user_data)
        user = db.get_user(user_id)

        # Notify admin about new user with auto-trial
        if OWNER_ID:
            first_name = message.get('from', {}).get('first_name', '')
            username = message.get('from', {}).get('username', '')
            name_display = f"@{username}" if username else first_name or f"ID_{user_id}"
            try:
                tg = get_telegram_service()
                keyboard = {
                    "inline_keyboard": [[
                        {"text": "❌ Отозвать триал", "callback_data": f"revoke_trial_{user_id}"}
                    ]]
                }
                tg.send_message(
                    OWNER_ID,
                    f"🆕 Новый пользователь: {name_display} (ID: {user_id})\n"
                    f"✅ Авто-триал: {TRIAL_MINUTES} мин",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.warning(f"Failed to notify owner about new user {user_id}: {e}")

    # Check for audio/voice/video
    if any(key in message for key in ['voice', 'audio', 'video', 'video_note']):
        return handle_audio_message(message, user)

    # Check for documents with audio/video MIME types
    if 'document' in message:
        doc = message['document']
        mime = doc.get('mime_type', '')
        if mime.startswith('audio/') or mime.startswith('video/'):
            return handle_audio_message(message, user)

    # Check for commands
    if text.startswith('/'):
        return handle_command(message, user)

    # Check for cloud drive URLs
    if text and _is_cloud_drive_url(text.strip()):
        return _handle_url_import(message, user, text.strip())

    return 'message_received'


def handle_audio_message(message: Dict[str, Any], user: Dict[str, Any]) -> str:
    """Handle audio/voice/video message."""
    chat_id = message.get('chat', {}).get('id')

    tg = get_telegram_service()

    # Get file info
    if 'voice' in message:
        file_id = message['voice']['file_id']
        duration = message['voice'].get('duration', 0)
        file_type = 'voice'
    elif 'audio' in message:
        file_id = message['audio']['file_id']
        duration = message['audio'].get('duration', 0)
        file_type = 'audio'
    elif 'video' in message:
        file_id = message['video']['file_id']
        duration = message['video'].get('duration', 0)
        file_type = 'video'
    elif 'video_note' in message:
        file_id = message['video_note']['file_id']
        duration = message['video_note'].get('duration', 0)
        file_type = 'video_note'
    elif 'document' in message:
        doc = message['document']
        file_id = doc['file_id']
        duration = doc.get('duration', 0)  # Telegram doesn't provide duration for documents
        file_type = 'document'
    else:
        return 'no_audio'

    # Check user balance with package recommendation
    balance = user.get('balance_minutes', 0)
    duration_minutes = (duration + 59) // 60  # Round up

    if balance < duration_minutes:
        deficit = duration_minutes - balance
        # Find smallest package covering deficit
        recommended = None
        for pkg in sorted(PRODUCT_PACKAGES.values(), key=lambda p: p['minutes']):
            if pkg['minutes'] >= deficit:
                recommended = pkg
                break

        msg = (
            f"⏱ Аудио: ~{duration_minutes} мин\n"
            f"💰 Ваш баланс: {balance} мин\n"
            f"📊 Не хватает: {deficit} мин\n\n"
        )
        if recommended:
            msg += f"Рекомендуем: <b>{recommended['title']}</b> ({recommended['minutes']} мин за {recommended['stars_amount']}⭐)\n"
        msg += "\n/buy_minutes — все пакеты"
        tg.send_message(chat_id, msg, parse_mode='HTML')
        return 'insufficient_balance'

    # Send processing notification and capture message_id for progress updates
    status_msg = tg.send_message(chat_id, "🎙 Аудио получено. Обрабатываю...")
    status_message_id = status_msg['result']['message_id'] if status_msg and status_msg.get('ok') else None

    # Short audio (<15s): sync for immediate response
    # Longer audio (>=15s): async for parallel processing + diarization for >=60s
    if duration >= SYNC_PROCESSING_THRESHOLD:
        return queue_audio_async(message, user, file_id, file_type, duration, status_message_id)
    else:
        return process_audio_sync(message, user, file_id, file_type, duration, status_message_id)


def process_audio_sync(message: Dict[str, Any], user: Dict[str, Any],
                       file_id: str, _file_type: str, duration: int,
                       status_message_id: Optional[int] = None) -> str:
    """Process audio synchronously (for short files)."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')

    tg = get_telegram_service()
    db = get_db_service()

    local_path = None
    converted_path = None

    logger.info(f"[routing] sync=True, duration={duration}s, user={user_id}")

    try:
        # Update progress: downloading
        if status_message_id:
            tg.edit_message_text(chat_id, status_message_id, "📥 Загружаю файл...")
        tg.send_chat_action(chat_id, 'typing')

        # Download file from Telegram
        telegram_file_path = tg.get_file_path(file_id)
        if not telegram_file_path:
            tg.send_message(chat_id, "Не удалось получить файл. Попробуйте ещё раз.")
            return 'download_failed'

        local_path = tg.download_file(telegram_file_path)
        if not local_path:
            tg.send_message(chat_id, "Не удалось скачать файл. Попробуйте ещё раз.")
            return 'download_failed'

        # Transcribe with Qwen-ASR
        from services.audio import AudioService
        # Read credentials at call time (module-level vars may be empty on FC 3.0 cold start)
        ak = ALIBABA_ACCESS_KEY or os.environ.get('ALIBABA_ACCESS_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
        sk = ALIBABA_SECRET_KEY or os.environ.get('ALIBABA_SECRET_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
        st = ALIBABA_SECURITY_TOKEN or os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN')
        audio_service = AudioService(
            whisper_backend='qwen-asr',
            oss_config={
                'bucket': os.environ.get('OSS_BUCKET', 'twbot-prod-audio'),
                'endpoint': os.environ.get('OSS_ENDPOINT', 'oss-eu-central-1.aliyuncs.com'),
                'access_key_id': ak,
                'access_key_secret': sk,
                'security_token': st,
            }
        )

        # For documents (duration=0), get actual duration via ffprobe
        if duration == 0 and local_path:
            actual_duration = audio_service.get_audio_duration(local_path)
            duration = int(actual_duration)
            duration_minutes = (duration + 59) // 60
            # Re-check balance with actual duration + package recommendation
            balance = user.get('balance_minutes', 0)
            if balance < duration_minutes:
                if status_message_id:
                    tg.delete_message(chat_id, status_message_id)
                deficit = duration_minutes - balance
                recommended = None
                for pkg in sorted(PRODUCT_PACKAGES.values(), key=lambda p: p['minutes']):
                    if pkg['minutes'] >= deficit:
                        recommended = pkg
                        break
                msg = (
                    f"⏱ Файл: {duration // 60} мин {duration % 60} сек\n"
                    f"💰 Ваш баланс: {balance} мин\n"
                    f"📊 Не хватает: {deficit} мин\n\n"
                )
                if recommended:
                    msg += f"Рекомендуем: <b>{recommended['title']}</b> ({recommended['minutes']} мин за {recommended['stars_amount']}⭐)\n"
                msg += "\n/buy_minutes — все пакеты"
                tg.send_message(chat_id, msg, parse_mode='HTML')
                os.remove(local_path)
                return 'insufficient_balance'

        # Update progress: transcribing
        if status_message_id:
            tg.edit_message_text(chat_id, status_message_id, "🎙 Распознаю речь...")
        tg.send_chat_action(chat_id, 'typing')

        converted_path = audio_service.prepare_audio_for_asr(local_path)
        if not converted_path:
            tg.send_message(chat_id, "Не удалось обработать аудио. Попробуйте другой формат.")
            return 'conversion_failed'

        # Extract settings from already-loaded user dict (avoid duplicate DB call)
        settings_json = user.get('settings', '{}')
        settings = json.loads(settings_json) if isinstance(settings_json, str) else (settings_json or {})
        use_code_tags = settings.get('use_code_tags', False)
        use_yo = settings.get('use_yo', True)

        # Diarization is NEVER run in sync path — it requires >60s (two-pass API polling).
        # Only the audio-processor (300s timeout) can run diarization.
        is_dialogue = False

        def chunk_progress(current, total):
            if status_message_id and total > 1:
                tg.edit_message_text(chat_id, status_message_id,
                    f"🎙 Распознаю речь... (часть {current} из {total})")

        text = audio_service.transcribe_audio(converted_path, progress_callback=chunk_progress)

        if not text or text.strip() == "Продолжение следует...":
            tg.send_message(chat_id, "На записи не обнаружено речи или текст не был распознан.")
            return 'no_speech'

        # Determine if audio was chunked (for LLM prompt)
        audio_duration = audio_service.get_audio_duration(converted_path)
        is_chunked = audio_duration > audio_service.ASR_MAX_CHUNK_DURATION

        # Format text with Qwen LLM (with Gemini fallback)
        if is_dialogue:
            if len(text) > 100:
                if status_message_id:
                    tg.edit_message_text(chat_id, status_message_id, "✏️ Форматирую диалог...")
                tg.send_chat_action(chat_id, 'typing')
                formatted_text = audio_service.format_text_with_llm(
                    text, use_code_tags=use_code_tags, use_yo=use_yo,
                    is_chunked=is_chunked, is_dialogue=True,
                    backend=settings.get('llm_backend', 'assemblyai'))  # Gemini 3 Flash default for dialogues
            else:
                formatted_text = text
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')
        elif len(text) > 100:
            if status_message_id:
                tg.edit_message_text(chat_id, status_message_id, "✏️ Форматирую текст...")
            tg.send_chat_action(chat_id, 'typing')
            formatted_text = audio_service.format_text_with_llm(
                text, use_code_tags=use_code_tags, use_yo=use_yo,
                is_chunked=is_chunked, is_dialogue=is_dialogue,
                backend=settings.get('llm_backend'))
        else:
            formatted_text = text
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

        # Send result: edit status message or send new one
        if use_code_tags:
            result_text = f"<code>{formatted_text}</code>"
            parse_mode = 'HTML'
        else:
            result_text = formatted_text
            parse_mode = ''

        # Deduct balance BEFORE delivery to prevent free transcriptions on failure
        duration_minutes = (duration + 59) // 60
        balance = user.get('balance_minutes', 0)
        balance_updated = db.update_user_balance(user_id, -duration_minutes)
        if not balance_updated:
            logger.error(f"CRITICAL: Failed to deduct {duration_minutes} min from user {user_id} balance!")
            try:
                owner_id = int(os.environ.get('OWNER_ID', 0))
                if owner_id:
                    tg.send_message(
                        owner_id,
                        f"⚠️ Ошибка списания баланса!\n"
                        f"User: {user_id}\n"
                        f"Минут: {duration_minutes}\n"
                        f"Требуется ручная корректировка."
                    )
            except Exception as notify_err:
                logger.error(f"Failed to notify owner about balance error: {notify_err}")

        # Deliver result to user
        long_text_mode = settings.get('long_text_mode', 'split')

        if status_message_id and len(result_text) <= 4000:
            tg.edit_message_text(chat_id, status_message_id, result_text, parse_mode=parse_mode)
        elif long_text_mode == 'file':
            if status_message_id:
                tg.delete_message(chat_id, status_message_id)
            first_dot = formatted_text.find('.')
            caption = (formatted_text[:first_dot+1] if 0 < first_dot < 200 else formatted_text[:200]) + "..."
            tg.send_as_file(chat_id, formatted_text, caption=caption)
        else:
            if status_message_id:
                tg.delete_message(chat_id, status_message_id)
            tg.send_long_message(chat_id, result_text, parse_mode=parse_mode)

        # Check for low balance warning (calculate from known values)
        if balance_updated:
            new_balance = max(0, int(balance) - duration_minutes)
            if 0 < new_balance < 5:
                tg.send_message(
                    chat_id,
                    f"⚠️ <b>Низкий баланс!</b>\n"
                    f"Осталось: {new_balance} мин.\n"
                    f"Пополнить: /buy_minutes",
                    parse_mode='HTML'
                )
            elif new_balance <= 0:
                tg.send_message(
                    chat_id,
                    f"❌ <b>Баланс исчерпан!</b>\n"
                    f"Пополнить: /buy_minutes",
                    parse_mode='HTML'
                )

        # Log transcription (after sending result to reduce perceived latency)
        db.log_transcription({
            'user_id': str(user_id),
            'duration': duration,
            'char_count': len(formatted_text),
            'status': 'completed'
        })

        return 'transcribed_sync'

    except Exception as e:
        logger.error(f"Error in sync processing: {e}", exc_info=True)
        # User-friendly error messages
        error_str = str(e).lower()
        if 'invalidparameter' in error_str or 'duration' in error_str:
            user_msg = "Аудио слишком длинное для обработки. Попробуйте отправить файл короче 60 минут."
        elif 'timeout' in error_str:
            user_msg = "Обработка заняла слишком много времени. Попробуйте файл поменьше."
        elif 'transcription empty' in error_str or 'no speech' in error_str:
            user_msg = "Не удалось распознать речь. Проверьте качество аудио."
        else:
            user_msg = "Произошла ошибка при обработке аудио. Попробуйте позже."
        tg.send_message(chat_id, user_msg)
        return 'error'

    finally:
        # Cleanup temp files on both success and error paths
        for path in (local_path, converted_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


def queue_audio_async(message: Dict[str, Any], user: Dict[str, Any],
                      file_id: str, file_type: str, duration: int,
                      status_message_id: Optional[int] = None) -> str:
    """Queue audio for async processing via direct HTTP invocation (or MNS fallback)."""
    import uuid

    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    message_id = message.get('message_id')

    db = get_db_service()
    tg = get_telegram_service()

    from services.utility import get_trace_id
    job_id = str(uuid.uuid4())
    job_data = {
        'job_id': job_id,
        'user_id': str(user_id),
        'chat_id': chat_id,
        'message_id': message_id,
        'file_id': file_id,
        'file_type': file_type,
        'duration': duration,
        'status': 'pending',
        'trace_id': get_trace_id(),
    }
    if status_message_id:
        job_data['status_message_id'] = status_message_id

    db.create_job(job_data)
    logger.info(f"[routing] sync=False, duration={duration}s, user={user_id}, job={job_id}")

    # Primary: direct HTTP invocation of audio-processor (fire-and-forget)
    import requests as http_req
    audio_processor_url = os.environ.get('AUDIO_PROCESSOR_URL')
    if audio_processor_url:
        try:
            # Connect timeout 10s (cold start), read timeout 1s (we don't wait for result)
            http_req.post(audio_processor_url, json=job_data, timeout=(10, 1))
            logger.info(f"HTTP invoked audio-processor for job {job_id}")
        except http_req.exceptions.ReadTimeout:
            # Expected: function started processing, we don't wait for the 60-300s result
            logger.info(f"HTTP invoked audio-processor for job {job_id} (fire-and-forget)")
        except (http_req.exceptions.ConnectionError, http_req.exceptions.Timeout) as e:
            logger.error(f"HTTP invoke failed for job {job_id}: {type(e).__name__}: {e}")
            # Fall through to MNS fallback
            audio_processor_url = None

    if audio_processor_url:
        if status_message_id:
            tg.edit_message_text(chat_id, status_message_id, "⏳ Обрабатываю аудио...")
        return 'queued'

    # Fallback: MNS queue
    mns_ak = ALIBABA_ACCESS_KEY or os.environ.get('ALIBABA_ACCESS_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
    mns_sk = ALIBABA_SECRET_KEY or os.environ.get('ALIBABA_SECRET_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')

    if MNS_ENDPOINT and mns_ak and mns_sk:
        try:
            from services.mns_service import MNSPublisher
            import json as json_module
            publisher = MNSPublisher(
                endpoint=MNS_ENDPOINT,
                access_key_id=mns_ak,
                access_key_secret=mns_sk
            )
            publisher.publish(AUDIO_JOBS_QUEUE, json_module.dumps(job_data).encode())
            logger.info(f"Published job {job_id} to MNS queue")
            if status_message_id:
                tg.edit_message_text(chat_id, status_message_id, "⏳ Аудио в очереди на обработку...")
            return 'queued'
        except Exception as e:
            logger.error(f"MNS publish failed for job {job_id}: {type(e).__name__}: {e}")

    # All async methods failed — sync fallback (no diarization)
    logger.warning(f"All async methods failed for job {job_id}, using sync fallback")
    try:
        db.update_job(job_id, {'status': 'failed', 'error': 'async_unavailable'})
    except Exception as e:
        logger.warning(f"Failed to update job {job_id} status before sync fallback: {e}")
    return process_audio_sync(message, user, file_id, file_type, duration, status_message_id)


def _cmd_start(chat_id, user_id, text, user, tg, db) -> str:
    balance = user.get('balance_minutes', 0)
    trial_status = user.get('trial_status', 'none')

    greeting = (
        "🎙 <b>Расшифровка аудио в текст</b>\n\n"
        "Интервью, совещание, лекция, подкаст — "
        "отправьте аудио и получите готовый текст с пунктуацией и абзацами.\n\n"
        "▸ Разбивка диалога по спикерам\n"
        "▸ Форматирование и «ё» через AI\n"
        "▸ Файлы до 500 МБ через /upload\n"
        "▸ Импорт по ссылке (Яндекс.Диск, Google Drive, Dropbox)\n\n"
    )

    if trial_status == 'approved' and balance > 0:
        greeting += (
            f"🎁 <b>{balance} бесплатных минут</b> уже на балансе.\n"
            "Перешлите голосовое сообщение, видео или кружок — попробуйте прямо сейчас.\n\n"
            "Обратная связь: @nafigator"
        )
    elif balance > 0:
        greeting += (
            f"💰 Баланс: <b>{balance} мин</b>\n"
            "Перешлите голосовое сообщение, видео или кружок для расшифровки.\n\n"
            "Обратная связь: @nafigator"
        )
    else:
        greeting += (
            "Баланс исчерпан. Пополните: /buy_minutes\n\n"
            "Обратная связь: @nafigator"
        )

    tg.send_message(chat_id, greeting, parse_mode='HTML')
    return 'start'


def _cmd_help(chat_id, user_id, text, user, tg, db) -> str:
    tg.send_message(
        chat_id,
        "📖 Справка по боту\n\n"
        "▸ Отправьте голосовое, аудио или видео — до 20 МБ\n"
        "▸ /upload — загрузите файл до 500 МБ\n"
        "▸ Скиньте ссылку с Яндекс.Диска, Google Drive или Dropbox\n\n"
        "Длинные аудио (интервью, лекции, пресс-конференции) — "
        "автоматическая разбивка по спикерам и LLM-форматирование.\n\n"
        "Команды:\n"
        "/balance - Проверить остаток минут\n"
        "/buy_minutes - Купить минуты\n"
        "/upload - Загрузка больших файлов (до 500 МБ)\n"
        "/settings - Показать настройки\n"
        "/code - Вкл/выкл моноширинный шрифт\n"
        "/yo - Вкл/выкл букву ё\n"
        "/output - Формат длинного текста (файл / сообщения)\n"
        "/speakers - Вкл/выкл метки спикеров"
    )
    return 'help'


def _cmd_balance(chat_id, user_id, text, user, tg, db) -> str:
    balance = user.get('balance_minutes', 0)
    tg.send_message(chat_id, f"💰 Ваш баланс: {balance} минут")
    return 'balance'


def _cmd_settings(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    use_code = settings.get('use_code_tags', False)
    use_yo = settings.get('use_yo', True)
    long_text_mode = settings.get('long_text_mode', 'split')
    speaker_labels = settings.get('speaker_labels', False)

    long_text_label = '\U0001f4c4 файл .txt' if long_text_mode == 'file' else '\U0001f4ac несколько сообщений'
    speakers_label = '\u2705 Вкл' if speaker_labels else '\u274c Выкл'

    tg.send_message(
        chat_id,
        f"⚙️ Ваши настройки:\n\n"
        f"Моноширинный шрифт: {'✅ Вкл' if use_code else '❌ Выкл'}\n"
        f"Использование ё: {'✅ Вкл' if use_yo else '❌ Выкл (заменяется на е)'}\n"
        f"Длинный текст: {long_text_label}\n"
        f"Метки спикеров: {speakers_label}\n\n"
        f"Команды для изменения:\n"
        f"/code - переключить шрифт\n"
        f"/yo - переключить букву ё\n"
        f"/output - формат длинного текста\n"
        f"/speakers - метки спикеров"
    )
    return 'settings'


def _cmd_code(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    settings['use_code_tags'] = not settings.get('use_code_tags', False)
    db.update_user_settings(user_id, settings)
    status = 'включено' if settings['use_code_tags'] else 'выключено'
    tg.send_message(chat_id, f"Моноширинный шрифт: {status}")
    return 'code_toggle'


def _cmd_yo(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    settings['use_yo'] = not settings.get('use_yo', True)
    db.update_user_settings(user_id, settings)
    status = 'включено' if settings['use_yo'] else 'замена на е'
    tg.send_message(chat_id, f"Использование буквы ё: {status}")
    return 'yo_toggle'


def _cmd_output(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    current = settings.get('long_text_mode', 'split')
    new_mode = 'file' if current == 'split' else 'split'
    settings['long_text_mode'] = new_mode
    db.update_user_settings(user_id, settings)
    label = '\U0001f4c4 файл .txt' if new_mode == 'file' else '\U0001f4ac несколько сообщений'
    tg.send_message(chat_id, f"Длинный текст: {label}")
    return 'output_toggle'


def _cmd_speakers(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    settings['speaker_labels'] = not settings.get('speaker_labels', False)
    db.update_user_settings(user_id, settings)
    status = 'включены' if settings['speaker_labels'] else 'выключены'
    tg.send_message(chat_id, f"Метки спикеров: {status}")
    return 'speakers_toggle'


def _cmd_buy_minutes(chat_id, user_id, text, user, tg, db) -> str:
    return handle_buy_minutes(chat_id, user_id, tg)


def _cmd_admin(chat_id, user_id, text, user, tg, db) -> str:
    admin_help = (
        "🔐 <b>Админ-команды</b>\n\n"
        "<b>Пользователи:</b>\n"
        "/user [search] — поиск пользователей\n"
        "/credit &lt;id&gt; &lt;мин&gt; — добавить минуты\n\n"
        "<b>Статистика:</b>\n"
        "/stat — общая статистика\n"
        "/cost — стоимость обработки\n"
        "/metrics [часы] — метрики производительности\n\n"
        "<b>Система:</b>\n"
        "/status — статус очереди MNS\n"
        "/flush — очистить зависшие задачи\n"
        "/batch [user_id] — очередь пользователя\n"
        "/mute [часы|off] — уведомления об ошибках\n"
        "/debug — вкл/выкл дебаг диаризации\n"
        "/llm [backend] — LLM backend (qwen/assemblyai)\n"
        "/llm &lt;user_id&gt; &lt;backend&gt; — LLM для юзера\n\n"
        "<b>Отчёты:</b>\n"
        "/export [users|logs|payments] [дни] — экспорт CSV\n"
        "/report [daily|weekly] — отчёт"
    )
    tg.send_message(chat_id, admin_help, parse_mode='HTML')
    return 'admin_help'


def _cmd_credit(chat_id, user_id, text, user, tg, db) -> str:
    return handle_credit_command(text, chat_id, tg, db)


def _cmd_user(chat_id, user_id, text, user, tg, db) -> str:
    return handle_user_search(text, chat_id, tg, db)


def _cmd_stat(chat_id, user_id, text, user, tg, db) -> str:
    return handle_stat_command(chat_id, tg, db)


def _cmd_cost(chat_id, user_id, text, user, tg, db) -> str:
    return handle_cost_command(chat_id, tg, db)


def _cmd_status(chat_id, user_id, text, user, tg, db) -> str:
    return handle_status_command(chat_id, tg, db)


def _cmd_flush(chat_id, user_id, text, user, tg, db) -> str:
    return handle_flush_command(chat_id, tg, db)


def _cmd_export(chat_id, user_id, text, user, tg, db) -> str:
    return handle_export_command(text, chat_id, tg, db)


def _cmd_report(chat_id, user_id, text, user, tg, db) -> str:
    return handle_report_command(text, chat_id, tg, db)


def _cmd_metrics(chat_id, user_id, text, user, tg, db) -> str:
    return handle_metrics_command(text, chat_id, tg, db)


def _cmd_batch(chat_id, user_id, text, user, tg, db) -> str:
    return handle_batch_command(text, chat_id, tg, db)


def _cmd_mute(chat_id, user_id, text, user, tg, db) -> str:
    return handle_mute_command(text, chat_id, tg)


def _cmd_debug(chat_id, user_id, text, user, tg, db) -> str:
    settings = db.get_user_settings(user_id) or {}
    settings['debug_mode'] = not settings.get('debug_mode', False)
    db.update_user_settings(user_id, settings)
    status = 'включён' if settings['debug_mode'] else 'выключен'
    tg.send_message(chat_id, f"Дебаг диаризации: {status}")
    return 'debug_toggle'


def _cmd_llm(chat_id, user_id, text, user, tg, db) -> str:
    parts = text.split() if text else []
    # /llm <user_id> <backend> — admin sets LLM for another user
    if len(parts) == 3 and str(user_id) == str(OWNER_ID):
        target_id = parts[1]
        backend = parts[2].lower()
        if backend == 'default':
            target_settings = db.get_user_settings(target_id) or {}
            target_settings.pop('llm_backend', None)
            db.update_user_settings(target_id, target_settings)
            tg.send_message(chat_id, f"LLM backend for user {target_id}: default")
            return 'llm_set_user'
        if backend in ('qwen', 'assemblyai'):
            target_settings = db.get_user_settings(target_id) or {}
            target_settings['llm_backend'] = backend
            db.update_user_settings(target_id, target_settings)
            tg.send_message(chat_id, f"LLM backend for user {target_id}: {backend}")
            return 'llm_set_user'
        else:
            tg.send_message(chat_id, "Backend: qwen / assemblyai / default")
            return 'llm_invalid'
    # /llm <backend> — set own LLM
    arg = parts[1].strip().lower() if len(parts) > 1 else None
    settings = db.get_user_settings(user_id) or {}
    current = settings.get('llm_backend', 'default')
    if arg == 'default':
        settings.pop('llm_backend', None)
        db.update_user_settings(user_id, settings)
        tg.send_message(chat_id, "LLM backend: default")
        return 'llm_set'
    if arg in ('qwen', 'assemblyai'):
        settings['llm_backend'] = arg
        db.update_user_settings(user_id, settings)
        tg.send_message(chat_id, f"LLM backend: {arg}")
        return 'llm_set'
    else:
        tg.send_message(
            chat_id,
            f"LLM backend: <b>{current}</b>\n\n"
            "/llm default\n/llm qwen\n/llm assemblyai\n"
            "/llm &lt;user_id&gt; &lt;backend&gt;",
            parse_mode='HTML',
        )
        return 'llm_show'


# Command dispatch tables
def _cmd_upload(chat_id, user_id, text, user, tg, db):
    """Send Mini App button for large file upload."""
    webhook_url = os.environ.get(
        'WEBHOOK_URL',
        'https://twbot-p-webhook-hnfnlkbphn.eu-central-1.fcapp.run')
    upload_url = f"{webhook_url.rstrip('/')}/upload"
    keyboard = {
        'inline_keyboard': [[{
            'text': '📎 Загрузить файл (до 500 МБ)',
            'web_app': {'url': upload_url}
        }]]
    }
    tg.send_message(
        chat_id,
        "📎 <b>Загрузка больших файлов</b>\n\n"
        "Нажмите кнопку ниже, чтобы загрузить аудио или видео файл размером до 500 МБ.\n\n"
        "Поддерживаемые форматы: MP3, WAV, OGG, M4A, FLAC, MP4, MOV, MKV, WEBM.\n\n"
        "💡 Файлы до 20 МБ можно отправлять обычным способом — "
        "просто перешлите аудио/видео боту.",
        parse_mode='HTML',
        reply_markup=keyboard
    )
    return 'upload_command'


_USER_COMMANDS = {
    '/start': _cmd_start,
    '/help': _cmd_help,
    '/balance': _cmd_balance,
    '/settings': _cmd_settings,
    '/code': _cmd_code,
    '/yo': _cmd_yo,
    '/output': _cmd_output,
    '/speakers': _cmd_speakers,
    '/buy_minutes': _cmd_buy_minutes,
    '/upload': _cmd_upload,
}

_ADMIN_COMMANDS = {
    '/admin': _cmd_admin,
    '/credit': _cmd_credit,
    '/user': _cmd_user,
    '/stat': _cmd_stat,
    '/cost': _cmd_cost,
    '/status': _cmd_status,
    '/flush': _cmd_flush,
    '/export': _cmd_export,
    '/report': _cmd_report,
    '/metrics': _cmd_metrics,
    '/batch': _cmd_batch,
    '/mute': _cmd_mute,
    '/debug': _cmd_debug,
    '/llm': _cmd_llm,
}


def handle_command(message: Dict[str, Any], user: Dict[str, Any]) -> str:
    """Handle bot commands via dispatch table."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    text = message.get('text', '')
    command = text.split()[0].lower().replace('@', '').split('@')[0]

    tg = get_telegram_service()
    db = get_db_service()

    # User commands (no auth required)
    handler = _USER_COMMANDS.get(command)
    if handler:
        return handler(chat_id, user_id, text, user, tg, db)

    # Admin commands (OWNER_ID required)
    handler = _ADMIN_COMMANDS.get(command)
    if handler:
        if user_id != OWNER_ID:
            return 'unauthorized'
        return handler(chat_id, user_id, text, user, tg, db)

    tg.send_message(chat_id, "Неизвестная команда. Используйте /help для справки.")
    return 'unknown_command'


def handle_mute_command(text: str, chat_id: int, tg) -> str:
    """Admin: /mute <hours> — mute error notifications. /mute off — unmute."""
    from services.utility import TelegramErrorHandler

    parts = text.split()

    if len(parts) < 2:
        muted = TelegramErrorHandler.is_muted()
        status = "🔇 уведомления выключены" if muted else "🔔 уведомления включены"
        tg.send_message(
            chat_id,
            f"{status}\n\n"
            "Формат: /mute <часы> — выключить\n"
            "/mute off — включить обратно",
        )
        return 'mute_status'

    action = parts[1].lower()
    if action == 'off':
        TelegramErrorHandler.clear_mute()
        tg.send_message(chat_id, "🔔 Уведомления об ошибках включены.")
        return 'mute_off'

    try:
        hours = float(action)
    except ValueError:
        tg.send_message(chat_id, "Формат: /mute 8 (часов) или /mute off")
        return 'mute_bad_format'

    TelegramErrorHandler.set_mute(hours)
    tg.send_message(chat_id, f"🔇 Уведомления об ошибках выключены на {hours}ч.")
    return 'mute_set'


# ==================== BUY COMMANDS ====================

def handle_buy_minutes(chat_id: int, _user_id: int, tg) -> str:
    """Show available packages with inline buttons."""
    msg = "💰 <b>Выберите пакет минут:</b>\n\n"

    keyboard = {"inline_keyboard": []}

    for _pkg_id, pkg in PRODUCT_PACKAGES.items():
        msg += f"<b>{pkg['title']}</b>\n"
        msg += f"  ⏱ {pkg['minutes']} мин | ⭐ {pkg['stars_amount']} звёзд\n\n"

        keyboard["inline_keyboard"].append([
            {"text": f"{pkg['title']} - {pkg['stars_amount']}⭐", "callback_data": pkg['payload']}
        ])

    tg.send_message(chat_id, msg, parse_mode='HTML', reply_markup=keyboard)
    return 'buy_minutes_shown'


# ==================== ADMIN COMMAND HANDLERS ====================

def handle_credit_command(text: str, chat_id: int, tg, db) -> str:
    """Handle /credit user_id minutes command."""
    parts = text.split()
    if len(parts) != 3:
        tg.send_message(chat_id, "Использование: /credit USER_ID MINUTES")
        return 'credit_usage'

    try:
        target_user_id = int(parts[1])
        minutes_to_add = float(parts[2])

        db.update_user_balance(target_user_id, minutes_to_add)
        tg.send_message(chat_id, f"✅ Начислено {minutes_to_add} минут пользователю {target_user_id}")

        # Notify the user
        try:
            if minutes_to_add == TRIAL_MINUTES:
                user_msg = f"🎉 Поздравляем! Ваша заявка на пробный доступ одобрена. Вам начислено {int(minutes_to_add)} минут."
            else:
                user_msg = f"💰 Вам начислено {int(minutes_to_add)} минут администратором."
            tg.send_message(target_user_id, user_msg)
        except Exception as e:
            logger.warning(f"Failed to notify user {target_user_id}: {e}")

        return 'credit_added'

    except ValueError:
        tg.send_message(chat_id, "❌ Ошибка: USER_ID должен быть числом, MINUTES - числом.")
        return 'credit_error'


def handle_user_search(text: str, chat_id: int, tg, db, page: int = 1) -> str:
    """Handle /user [search] or /user --page N command."""
    parts = text.split()
    search_query = None

    # Parse command: /user [search] or /user --page N or /user -p N
    if len(parts) > 1:
        # Check for explicit page flag: /user --page 2 or /user -p 2
        if parts[1] in ('--page', '-p') and len(parts) > 2 and parts[2].isdigit():
            page = int(parts[2])
        else:
            # Everything else is a search query (including numbers - search by ID)
            search_query = ' '.join(parts[1:])

    PAGE_SIZE = 20

    if search_query:
        users = db.search_users(search_query)
        if not users:
            tg.send_message(chat_id, f"❌ Пользователи по запросу '{search_query}' не найдены.")
            return 'user_not_found'
        title_prefix = f"🔍 <b>Поиск '{search_query}'</b>"
    else:
        users = db.get_all_users(limit=100)
        title_prefix = "👥 <b>Все пользователи</b>"

    total = len(users)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_users = users[start_idx:end_idx]

    title = f"{title_prefix} ({start_idx + 1}-{min(end_idx, total)} из {total}):\n\n"

    msg = title
    for idx, user in enumerate(page_users, start_idx + 1):
        uid = user.get('user_id', 'unknown')
        # Support both old user_name and new first_name/last_name
        name = user.get('first_name', '') + ' ' + user.get('last_name', '')
        name = name.strip()
        # Only use user_name if it doesn't look like a placeholder
        if not name:
            user_name = user.get('user_name', '')
            if user_name and not user_name.startswith('User_') and not user_name.startswith('ID_'):
                name = user_name
            else:
                name = f"ID_{uid}"
        balance = user.get('balance_minutes', 0)
        trial_status = user.get('trial_status', 'none')

        trial_emoji = ""
        if trial_status == 'approved':
            trial_emoji = "✅"
        elif trial_status == 'pending':
            trial_emoji = "⏳"
        elif trial_status == 'denied':
            trial_emoji = "❌"

        msg += f"{idx}. {name} (ID: <code>{uid}</code>)\n"
        msg += f"   💰 {balance} мин"
        if trial_emoji:
            msg += f" | {trial_emoji}"
        msg += "\n\n"

    # Pagination buttons
    reply_markup = None
    if total_pages > 1:
        msg += f"\n<i>Страница {page}/{total_pages}</i>"
        buttons = []
        if page > 1:
            buttons.append({"text": "← Назад", "callback_data": f"users_page_{page - 1}"})
        if page < total_pages:
            buttons.append({"text": "Вперёд →", "callback_data": f"users_page_{page + 1}"})
        if buttons:
            reply_markup = {"inline_keyboard": [buttons]}

    tg.send_message(chat_id, msg[:4000], parse_mode='HTML', reply_markup=reply_markup)
    return 'users_listed'


def handle_stat_command(chat_id: int, tg, db) -> str:
    """Handle /stat command - usage statistics."""
    stats = db.get_transcription_stats(days=30)

    total_count = stats.get('total_count', 0)
    total_seconds = stats.get('total_seconds', 0)
    total_chars = stats.get('total_chars', 0)
    total_minutes = total_seconds / 60

    users = db.get_all_users()
    total_users = len(users)
    active_users = len(stats.get('user_stats', {}))

    msg = "📊 <b>Статистика использования (30 дней)</b>\n\n"
    msg += f"👥 Всего пользователей: {total_users}\n"
    msg += f"👤 Активных: {active_users}\n\n"
    msg += f"🎵 <b>Обработка:</b>\n"
    msg += f"  • Транскрипций: {total_count}\n"
    msg += f"  • Минут обработано: {total_minutes:.1f}\n"
    msg += f"  • Символов: {total_chars:,}\n"

    # Top users
    user_stats = stats.get('user_stats', {})
    if user_stats:
        sorted_users = sorted(user_stats.items(), key=lambda x: x[1]['duration'], reverse=True)[:5]
        msg += "\n🏆 <b>Топ пользователей:</b>\n"
        for i, (uid, udata) in enumerate(sorted_users, 1):
            user = db.get_user(int(uid)) if uid.isdigit() else None
            name = user.get('first_name', f'ID_{uid}') if user else f'ID_{uid}'
            minutes = udata['duration'] / 60
            msg += f"  {i}. {name}: {minutes:.1f} мин\n"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'stat_shown'


def handle_cost_command(chat_id: int, tg, db) -> str:
    """Handle /cost command - cost analysis."""
    stats = db.get_transcription_stats(days=30)

    total_count = stats.get('total_count', 0)
    total_seconds = stats.get('total_seconds', 0)
    total_chars = stats.get('total_chars', 0)

    if total_count == 0:
        tg.send_message(chat_id, "📊 Нет данных за последние 30 дней.")
        return 'no_cost_data'

    total_minutes = total_seconds / 60

    # Alibaba pricing (Qwen3-ASR + Qwen-turbo)
    # Qwen3-ASR: approximately $0.002 per minute
    # Qwen-turbo: approximately $0.0001 per 1000 chars
    asr_cost = total_minutes * 0.002
    llm_cost = (total_chars / 1000) * 0.0001

    # Infrastructure (Alibaba FC + Tablestore + MNS)
    fc_cost = 5  # ~$5/month estimate
    tablestore_cost = 1  # ~$1/month estimate
    mns_cost = 0.5  # ~$0.5/month estimate
    infra_cost = fc_cost + tablestore_cost + mns_cost

    total_api_cost = asr_cost + llm_cost
    total_cost = total_api_cost + infra_cost

    cost_per_minute = total_cost / total_minutes if total_minutes > 0 else 0

    msg = f"""💰 <b>Расчет стоимости за 30 дней</b>
📍 <i>Alibaba Cloud (eu-central-1)</i>

📊 Обработано: {total_count} файлов
⏱ Общая длительность: {math.ceil(total_minutes)} минут
📝 Символов: {total_chars:,}

💵 <b>API расходы:</b>
• Qwen3-ASR: ${asr_cost:.3f}
• Qwen-turbo LLM: ${llm_cost:.3f}
• <b>Итого API: ${total_api_cost:.3f}</b>

🏗 <b>Инфраструктура (оценка):</b>
• Function Compute: ${fc_cost:.2f}
• Tablestore: ${tablestore_cost:.2f}
• MNS: ${mns_cost:.2f}
• <b>Итого инфра: ${infra_cost:.2f}</b>

💰 <b>ОБЩИЕ РАСХОДЫ: ${total_cost:.2f}</b>
📈 <b>Себестоимость минуты: ${cost_per_minute:.4f}</b>"""

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'cost_shown'


def handle_status_command(chat_id: int, tg, db) -> str:
    """Handle /status command - queue status."""
    queue_count = db.count_pending_jobs()

    msg = "📊 <b>Статус очереди обработки</b>\n\n"

    if queue_count == 0:
        msg += "✅ Очередь пуста."
    else:
        msg += f"📥 Всего в очереди: {queue_count}\n\n"

        pending_jobs = db.get_pending_jobs(limit=10)
        if pending_jobs:
            msg += "Активные задачи:\n"
            for job in pending_jobs:
                job_user_id = job.get('user_id', 'unknown')
                status = job.get('status', 'unknown')
                duration = job.get('duration', 0)
                msg += f"• User {job_user_id} - {status} ({duration}s)\n"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'status_shown'


def handle_flush_command(chat_id: int, tg, db) -> str:
    """Handle /flush command - clean stuck jobs."""
    stuck_jobs = db.get_stuck_jobs(hours_threshold=1)

    if not stuck_jobs:
        tg.send_message(chat_id, "✅ Нет застрявших задач в очереди.")
        return 'no_stuck_jobs'

    deleted_count = 0
    refunded_users = {}

    for job in stuck_jobs:
        job_id = job.get('job_id')
        job_user_id = job.get('user_id')
        duration = job.get('duration', 0)

        # Delete the job
        if db.delete_job(job_id):
            deleted_count += 1

        # Track refunds
        if job_user_id and duration > 0:
            if job_user_id not in refunded_users:
                refunded_users[job_user_id] = 0
            refunded_users[job_user_id] += duration

    # Apply refunds
    for refund_user_id, total_seconds in refunded_users.items():
        minutes_to_refund = total_seconds / 60
        try:
            db.update_user_balance(int(refund_user_id), minutes_to_refund)
        except Exception as e:
            logger.warning(f"Failed to refund {minutes_to_refund:.1f} min to user {refund_user_id}: {e}")

    msg = f"🧹 <b>Очистка завершена</b>\n\n"
    msg += f"✅ Удалено задач: {deleted_count}\n"
    msg += f"💰 Возвращено минут {len(refunded_users)} пользователям"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'flush_done'


def handle_export_command(text: str, chat_id: int, tg, db) -> str:
    """Handle /export [users|logs|payments] [days] command."""
    parts = text.split()
    export_type = parts[1] if len(parts) > 1 else 'users'
    days = int(parts[2]) if len(parts) > 2 else 30

    if export_type not in ['users', 'logs', 'payments']:
        tg.send_message(chat_id, "❌ Использование: /export [users|logs|payments] [дней]")
        return 'export_usage'

    try:
        import tempfile

        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8-sig')
        csv_writer = csv.writer(temp_file)

        if export_type == 'users':
            csv_writer.writerow(['User ID', 'Name', 'Username', 'Balance', 'Trial Status', 'Created At'])
            users = db.get_all_users(limit=1000)
            for user in users:
                csv_writer.writerow([
                    user.get('user_id', ''),
                    user.get('first_name', ''),
                    user.get('username', ''),
                    user.get('balance_minutes', 0),
                    user.get('trial_status', ''),
                    user.get('created_at', '')
                ])
            caption = f"📊 Экспорт пользователей\nВсего: {len(users)}"

        elif export_type == 'logs':
            stats = db.get_transcription_stats(days=days)
            csv_writer.writerow(['Period', 'Total Count', 'Total Minutes', 'Total Chars'])
            csv_writer.writerow([
                f'Last {days} days',
                stats.get('total_count', 0),
                stats.get('total_seconds', 0) / 60,
                stats.get('total_chars', 0)
            ])
            caption = f"📊 Экспорт статистики за {days} дней"

        else:  # payments
            payment_stats = db.get_payment_stats(days=days)
            csv_writer.writerow(['Period', 'Total Payments', 'Total Stars', 'Total Minutes'])
            csv_writer.writerow([
                f'Last {days} days',
                payment_stats.get('total_count', 0),
                payment_stats.get('total_stars', 0),
                payment_stats.get('total_minutes', 0)
            ])
            caption = f"💰 Экспорт платежей за {days} дней"

        temp_file.close()
        tg.send_document(chat_id, temp_file.name, caption=caption)

        import os
        os.unlink(temp_file.name)

        return 'export_done'

    except Exception as e:
        logger.error(f"Error in export: {e}")
        tg.send_message(chat_id, f"❌ Ошибка при экспорте: {str(e)}")
        return 'export_error'


def handle_report_command(text: str, chat_id: int, tg, db) -> str:
    """Handle /report [daily|weekly] command."""
    parts = text.split()
    report_type = parts[1] if len(parts) > 1 else 'daily'

    if report_type not in ['daily', 'weekly']:
        tg.send_message(chat_id, "❌ Использование: /report [daily|weekly]")
        return 'report_usage'

    days = 1 if report_type == 'daily' else 7
    period_name = "Ежедневный" if report_type == 'daily' else "Еженедельный"

    stats = db.get_transcription_stats(days=days)
    payment_stats = db.get_payment_stats(days=days)
    users = db.get_all_users()

    msg = f"📊 <b>{period_name} отчет</b>\n"
    msg += f"📅 Период: последние {days} дней\n\n"

    msg += f"👥 <b>Пользователи:</b>\n"
    msg += f"  • Всего: {len(users)}\n"
    msg += f"  • Активных: {len(stats.get('user_stats', {}))}\n\n"

    msg += f"🎵 <b>Обработка:</b>\n"
    msg += f"  • Транскрипций: {stats.get('total_count', 0)}\n"
    msg += f"  • Минут: {stats.get('total_seconds', 0) / 60:.1f}\n\n"

    msg += f"💰 <b>Доходы:</b>\n"
    msg += f"  • Платежей: {payment_stats.get('total_count', 0)}\n"
    msg += f"  • Stars: {payment_stats.get('total_stars', 0)} ⭐\n"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'report_shown'


def handle_metrics_command(text: str, chat_id: int, tg, db) -> str:
    """Handle /metrics [hours] command."""
    parts = text.split()
    hours = int(parts[1]) if len(parts) > 1 else 24

    # For now, just show basic metrics
    stats = db.get_transcription_stats(days=1)
    queue_count = db.count_pending_jobs()

    msg = f"📈 <b>Метрики за последние {hours} часов</b>\n\n"
    msg += f"🎵 Транскрипций: {stats.get('total_count', 0)}\n"
    msg += f"⏱ Минут обработано: {stats.get('total_seconds', 0) / 60:.1f}\n"
    msg += f"📥 В очереди: {queue_count}\n"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'metrics_shown'


def handle_batch_command(text: str, chat_id: int, tg, db) -> str:
    """Handle /batch [user_id] command - show user's queue."""
    parts = text.split()

    if len(parts) < 2:
        tg.send_message(chat_id, "Использование: /batch USER_ID")
        return 'batch_usage'

    target_user_id = parts[1]
    pending_jobs = db.get_pending_jobs(limit=50)

    user_jobs = [j for j in pending_jobs if str(j.get('user_id')) == target_user_id]

    if not user_jobs:
        tg.send_message(chat_id, f"✅ Нет задач в очереди для пользователя {target_user_id}")
        return 'no_batch_jobs'

    msg = f"📋 <b>Очередь пользователя {target_user_id}</b>\n\n"
    for job in user_jobs:
        job_id = job.get('job_id', 'unknown')[:8]
        status = job.get('status', 'unknown')
        duration = job.get('duration', 0)
        msg += f"• {job_id}... - {status} ({duration}s)\n"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'batch_shown'


def handle_callback_query(callback_query: Dict[str, Any]) -> str:
    """Handle callback query from inline keyboard."""
    callback_data = callback_query.get('data', '')
    user_id = callback_query.get('from', {}).get('id')
    chat_id = callback_query.get('message', {}).get('chat', {}).get('id')
    message_id = callback_query.get('message', {}).get('message_id')
    callback_id = callback_query.get('id', '')

    logger.info(f"Callback query from {user_id}: {callback_data}")

    tg = get_telegram_service()
    db = get_db_service()

    # Acknowledge the callback
    tg.answer_callback_query(callback_id)

    # Only owner can use admin callbacks
    if user_id != OWNER_ID:
        # Check for buy_ callbacks (available to all users)
        if callback_data.startswith('buy_'):
            return handle_buy_callback(callback_data, user_id, chat_id)
        return 'unauthorized_callback'

    # Revoke auto-trial
    if callback_data.startswith('revoke_trial_'):
        target_user_id = int(callback_data.replace('revoke_trial_', ''))
        return handle_trial_revoke(target_user_id, chat_id, message_id, tg, db)

    # User management
    if callback_data.startswith('add_minutes_'):
        target_user_id = int(callback_data.replace('add_minutes_', ''))
        tg.send_message(chat_id, f"Для добавления минут используйте:\n/credit {target_user_id} [КОЛИЧЕСТВО]")
        return 'add_minutes_prompt'

    if callback_data.startswith('user_details_'):
        target_user_id = int(callback_data.replace('user_details_', ''))
        return show_user_details(target_user_id, chat_id, tg, db)

    if callback_data.startswith('delete_user_'):
        target_user_id = int(callback_data.replace('delete_user_', ''))
        tg.send_message(chat_id, f"⚠️ Удаление пользователя {target_user_id} пока не реализовано.")
        return 'delete_user_pending'

    # Buy callbacks (also for owner)
    if callback_data.startswith('buy_'):
        return handle_buy_callback(callback_data, user_id, chat_id)

    # Pagination for /user list
    if callback_data.startswith('users_page_'):
        page = int(callback_data.replace('users_page_', ''))
        # Delete old message and send new one with updated page
        tg.delete_message(chat_id, message_id)
        return handle_user_search(f'/user --page {page}', chat_id, tg, db, page)

    return f'callback_{callback_data}'


def handle_trial_revoke(target_user_id: int, chat_id: int, message_id: int,
                        tg, db) -> str:
    """Revoke auto-trial: set balance to 0, mark trial as denied."""
    db.update_user(target_user_id, {'trial_status': 'denied', 'balance_minutes': 0})
    tg.edit_message_text(chat_id, message_id,
        f"❌ Триал пользователя {target_user_id} отозван. Баланс обнулён.")
    logger.info(f"Auto-trial revoked for user {target_user_id}")
    return 'trial_revoked'


def show_user_details(target_user_id: int, chat_id: int, tg, db) -> str:
    """Show detailed user information."""
    user = db.get_user(target_user_id)
    if not user:
        tg.send_message(chat_id, f"❌ Пользователь {target_user_id} не найден.")
        return 'user_not_found'

    name = user.get('first_name', '') + ' ' + user.get('last_name', '')
    name = name.strip() or f"ID_{target_user_id}"
    username = user.get('username', '')
    balance = user.get('balance_minutes', 0)
    trial_status = user.get('trial_status', 'none')
    created_at = user.get('created_at', 'Неизвестно')

    msg = f"📋 <b>Информация о пользователе</b>\n\n"
    msg += f"👤 Имя: {name}\n"
    if username:
        msg += f"📝 Username: @{username}\n"
    msg += f"🆔 ID: {target_user_id}\n"
    msg += f"💰 Баланс: {balance} мин\n"
    msg += f"🎫 Триал: {trial_status}\n"
    msg += f"📅 Регистрация: {created_at}\n\n"
    msg += f"<b>Действия:</b>\n"
    msg += f"/credit {target_user_id} [минуты] - добавить минуты"

    tg.send_message(chat_id, msg, parse_mode='HTML')
    return 'user_details_shown'


def handle_buy_callback(callback_data: str, user_id: int, chat_id: int) -> str:
    """Handle buy package callback."""
    tg = get_telegram_service()
    db = get_db_service()

    # Find package by payload
    package = None
    for pkg_id, pkg_data in PRODUCT_PACKAGES.items():
        if pkg_data['payload'] == callback_data:
            package = pkg_data
            package['id'] = pkg_id
            break

    if not package:
        tg.send_message(chat_id, "❌ Пакет не найден.")
        return 'package_not_found'

    # Check micro package limit
    if package['id'] == 'micro_10':
        user = db.get_user(user_id)
        micro_purchases = user.get('micro_package_purchases', 0) if user else 0
        if micro_purchases >= package.get('purchase_limit', 3):
            tg.send_message(chat_id,
                "❌ Вы уже исчерпали лимит покупок промо-пакета 'Микро'. "
                "Выберите другой пакет с помощью команды /buy_minutes")
            return 'micro_limit_reached'

    # Send invoice
    tg.send_invoice(
        chat_id=chat_id,
        title=package['title'],
        description=package['description'],
        payload=f"minutes_{package['minutes']}",
        currency="XTR",
        prices=[{"label": "Stars", "amount": package['stars_amount']}]
    )

    return 'invoice_sent'


def handle_pre_checkout(pre_checkout_query: Dict[str, Any]) -> str:
    """Handle pre-checkout query for Telegram Payments.

    Validates payload format and currency before approving.
    """
    tg = get_telegram_service()
    query_id = pre_checkout_query.get('id')
    if not query_id:
        logger.error("Pre-checkout query missing id")
        return 'pre_checkout_error'

    # Validate payload matches a known product
    payload = pre_checkout_query.get('invoice_payload', '')
    valid_payloads = {pkg['payload'] for pkg in PRODUCT_PACKAGES.values()}
    if payload not in valid_payloads:
        logger.warning(f"Pre-checkout rejected: unknown payload '{payload}'")
        tg.answer_pre_checkout_query(query_id, ok=False,
            error_message="Неизвестный продукт. Попробуйте ещё раз через /buy_minutes")
        return 'pre_checkout_rejected'

    # Validate currency
    currency = pre_checkout_query.get('currency', '')
    if currency != 'XTR':
        logger.warning(f"Pre-checkout rejected: unexpected currency '{currency}'")
        tg.answer_pre_checkout_query(query_id, ok=False,
            error_message="Неверная валюта платежа.")
        return 'pre_checkout_rejected'

    tg.answer_pre_checkout_query(query_id, ok=True)
    return 'pre_checkout_approved'


def handle_successful_payment(message: Dict[str, Any]) -> str:
    """Handle successful payment."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    payment = message.get('successful_payment', {})

    logger.info(f"Processing successful payment for user {user_id}: {payment}")

    tg = get_telegram_service()
    db = get_db_service()

    # Parse invoice payload
    payload = payment.get('invoice_payload', '')
    # Payload format: "minutes_XXX" where XXX is the number of minutes

    try:
        minutes = int(payload.split('_')[1])
    except (IndexError, ValueError) as e:
        logger.error(f"Could not parse payment payload '{payload}': {e}")
        tg.send_message(chat_id, "❌ Ошибка обработки платежа. Свяжитесь с поддержкой.")
        return 'payment_parse_error'

    if minutes > 0:
        success = db.update_user_balance(user_id, minutes)
        if success:
            logger.info(f"Balance updated for user {user_id}: +{minutes} minutes")
            db.log_payment({
                'user_id': str(user_id),
                'minutes_added': minutes,
                'stars_amount': payment.get('total_amount', 0),
                'telegram_payment_charge_id': payment.get('telegram_payment_charge_id', '')
            })
            # Update micro package purchase counter
            if minutes == 10:
                db.increment_micro_purchases(user_id)
            tg.send_message(chat_id, f"✅ Оплата прошла успешно! Добавлено {minutes} минут.")
        else:
            logger.error(f"Failed to update balance for user {user_id}")
            tg.send_message(chat_id, "❌ Ошибка зачисления минут. Свяжитесь с поддержкой.")
            return 'balance_update_failed'

    return 'payment_processed'


# === Cloud Drive Import ===

CLOUD_DRIVE_PATTERNS = {
    'yandex_disk': r'https?://disk\.yandex\.\w+/[di]/\S+',
    'google_drive': r'https?://drive\.google\.com/file/d/([^/]+)',
    'dropbox': r'https?://(?:www\.)?dropbox\.com/s\w*/\S+',
}


def _is_cloud_drive_url(text: str) -> bool:
    """Check if text contains a cloud drive URL."""
    import re
    for pattern in CLOUD_DRIVE_PATTERNS.values():
        if re.search(pattern, text):
            return True
    return False


def _resolve_download_url(url: str) -> Optional[str]:
    """Convert cloud drive share URL to direct download URL.

    Returns direct URL or None if service not supported.
    """
    import re
    import requests as req

    # Yandex.Disk
    match = re.search(CLOUD_DRIVE_PATTERNS['yandex_disk'], url)
    if match:
        public_url = match.group(0)
        try:
            api_url = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={public_url}"
            resp = req.get(api_url, timeout=10)
            if resp.status_code == 200:
                return resp.json().get('href')
        except Exception as e:
            logger.warning(f"Yandex.Disk resolve failed: {e}")
        return None

    # Google Drive
    match = re.search(CLOUD_DRIVE_PATTERNS['google_drive'], url)
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    # Dropbox
    match = re.search(CLOUD_DRIVE_PATTERNS['dropbox'], url)
    if match:
        dropbox_url = match.group(0)
        # Replace dl=0 with dl=1 for direct download
        if 'dl=0' in dropbox_url:
            return dropbox_url.replace('dl=0', 'dl=1')
        elif 'dl=' not in dropbox_url:
            separator = '&' if '?' in dropbox_url else '?'
            return f"{dropbox_url}{separator}dl=1"
        return dropbox_url

    return None


def _handle_url_import(message: Dict[str, Any], user: Dict[str, Any], url: str) -> str:
    """Handle audio/video URL from cloud drive."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    tg = get_telegram_service()
    db = get_db_service()

    # Check balance (at least 1 minute)
    balance = user.get('balance_minutes', 0)
    if balance < 1:
        tg.send_message(chat_id,
            "💰 Баланс: 0 мин. Для обработки файла нужен хотя бы 1 мин.\n/buy_minutes")
        return 'insufficient_balance'

    # Resolve download URL
    download_url = _resolve_download_url(url)
    if not download_url:
        tg.send_message(chat_id, "❌ Не удалось получить ссылку для скачивания. "
                        "Поддерживаются: Яндекс.Диск, Google Drive, Dropbox.")
        return 'unsupported_url'

    # Detect service name for user message
    import re
    if re.search(CLOUD_DRIVE_PATTERNS['yandex_disk'], url):
        service_name = 'Яндекс.Диск'
    elif re.search(CLOUD_DRIVE_PATTERNS['google_drive'], url):
        service_name = 'Google Drive'
    else:
        service_name = 'Dropbox'

    status_msg = tg.send_message(chat_id,
        f"🔗 Ссылка {service_name} обнаружена.\n📥 Скачиваю файл...")
    status_message_id = status_msg['result']['message_id'] if status_msg and status_msg.get('ok') else None

    # Create job and queue for async processing
    import uuid
    job_id = str(uuid.uuid4())

    db.create_job({
        'job_id': job_id,
        'user_id': str(user_id),
        'chat_id': str(chat_id),
        'file_id': download_url,
        'file_type': 'url_import',
        'duration': 0,
        'status': 'pending',
        'status_message_id': str(status_message_id) if status_message_id else '',
    })

    job_data = {
        'job_id': job_id,
        'user_id': str(user_id),
        'chat_id': str(chat_id),
        'file_id': download_url,
        'file_type': 'url_import',
        'duration': 0,
        'status_message_id': str(status_message_id) if status_message_id else '',
    }

    # Try async via MNS
    if MNS_ENDPOINT and ALIBABA_ACCESS_KEY and ALIBABA_SECRET_KEY:
        from services.mns_service import MNSService, MNSPublisher
        mns = MNSService(
            endpoint=MNS_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            queue_name=os.environ.get('AUDIO_JOBS_QUEUE',
                                       'telegram-whisper-bot-prod-audio-jobs')
        )
        publisher = MNSPublisher(mns)
        if publisher.publish(job_data):
            logger.info(f"[cloud-import] job {job_id} queued, url={url[:60]}")
            return 'url_import_queued'

    # Fallback: process sync (not ideal for large files, but better than nothing)
    logger.warning("MNS not available for URL import, rejecting")
    tg.send_message(chat_id, "❌ Сервис временно недоступен. Попробуйте позже.")
    db.update_job(job_id, {'status': 'failed', 'error': 'MNS unavailable'})
    return 'url_import_failed'


# === Mini App: Large File Upload ===

UPLOAD_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Upload Audio</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: var(--tg-theme-bg-color, #fff);
       color: var(--tg-theme-text-color, #000); padding: 16px; }
.drop-zone { border: 2px dashed var(--tg-theme-hint-color, #aaa); border-radius: 12px;
             padding: 40px 20px; text-align: center; margin: 20px 0; cursor: pointer;
             transition: border-color 0.3s; }
.drop-zone.active { border-color: var(--tg-theme-button-color, #3390ec); background: rgba(51,144,236,0.05); }
.drop-zone h2 { margin-bottom: 8px; font-size: 18px; }
.drop-zone p { color: var(--tg-theme-hint-color, #999); font-size: 14px; }
.progress-bar { width: 100%; height: 8px; background: var(--tg-theme-secondary-bg-color, #eee);
                border-radius: 4px; margin: 16px 0; overflow: hidden; display: none; }
.progress-bar .fill { height: 100%; background: var(--tg-theme-button-color, #3390ec);
                      border-radius: 4px; transition: width 0.3s; width: 0%; }
.status { text-align: center; margin: 12px 0; font-size: 14px;
          color: var(--tg-theme-hint-color, #999); }
.error { color: #e53935; }
input[type=file] { display: none; }
.limits { font-size: 12px; color: var(--tg-theme-hint-color, #999); text-align: center; margin-top: 8px; }
</style>
</head>
<body>
<div class="drop-zone" id="dropZone">
  <h2>📎 Перетащите файл сюда</h2>
  <p>или нажмите для выбора</p>
  <p class="limits">MP3, WAV, OGG, M4A, FLAC, MP4, MOV, MKV, WEBM — до 500 МБ</p>
</div>
<input type="file" id="fileInput" accept="audio/*,video/*,.mp3,.wav,.ogg,.m4a,.flac,.mp4,.mov,.mkv,.webm">
<div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>
<div class="status" id="status"></div>

<script>
const API_BASE = window.location.origin;
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const ALLOWED = ['audio/mpeg','audio/wav','audio/ogg','audio/x-m4a','audio/flac','audio/aac',
                 'audio/mp4','video/mp4','video/quicktime','video/x-matroska','video/webm',
                 'audio/x-wav','audio/wave'];
const ALLOWED_EXT = ['.mp3','.wav','.ogg','.m4a','.flac','.aac','.mp4','.mov','.mkv','.webm'];
const MAX_SIZE = 500 * 1024 * 1024;

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const progressBar = document.getElementById('progressBar');
const progressFill = document.getElementById('progressFill');
const status = document.getElementById('status');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('active'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('active'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('active');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFile(fileInput.files[0]); });

function validateFile(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!ALLOWED.includes(file.type) && !ALLOWED_EXT.includes(ext)) {
    return 'Неподдерживаемый формат файла';
  }
  if (file.size > MAX_SIZE) {
    return 'Файл слишком большой (макс. 500 МБ)';
  }
  return null;
}

async function handleFile(file) {
  const err = validateFile(file);
  if (err) { showError(err); return; }

  try {
    showStatus('Запрашиваю URL для загрузки...');
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    const initData = tg.initData || '';

    const urlRes = await fetch(API_BASE + '/api/signed-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ext: ext, init_data: initData })
    });
    if (!urlRes.ok) { const e = await urlRes.json(); throw new Error(e.error || 'URL error'); }
    const { put_url, oss_key } = await urlRes.json();

    showStatus('Загружаю файл...');
    progressBar.style.display = 'block';

    await uploadWithProgress(put_url, file);

    showStatus('Отправляю на обработку...');
    const procRes = await fetch(API_BASE + '/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oss_key: oss_key, init_data: initData, filename: file.name })
    });
    if (!procRes.ok) { const e = await procRes.json(); throw new Error(e.error || 'Process error'); }

    showStatus('✅ Файл отправлен на обработку! Результат придёт в чат.');
    progressFill.style.width = '100%';
    setTimeout(() => tg.close(), 2000);

  } catch (e) {
    showError(e.message || 'Ошибка загрузки');
  }
}

function uploadWithProgress(url, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', e => {
      if (e.lengthComputable) {
        const pct = Math.round(e.loaded / e.total * 100);
        progressFill.style.width = pct + '%';
        showStatus('Загружаю... ' + pct + '%');
      }
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error('Upload failed: ' + xhr.status));
    });
    xhr.addEventListener('error', () => reject(new Error('Network error')));
    xhr.open('PUT', url);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.send(file);
  });
}

function showStatus(msg) { status.textContent = msg; status.classList.remove('error'); }
function showError(msg) { status.textContent = msg; status.classList.add('error');
                          progressBar.style.display = 'none'; }
</script>
</body>
</html>"""


def _serve_upload_page():
    """Serve Mini App HTML for large file upload."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'no-cache',
        },
        'body': UPLOAD_PAGE_HTML
    }


def _validate_init_data(init_data: str) -> Optional[int]:
    """Validate Telegram Mini App initData using HMAC-SHA256.

    Returns user_id if valid, None otherwise.
    """
    import hashlib
    import hmac
    from urllib.parse import parse_qs

    if not init_data:
        return None

    try:
        params = parse_qs(init_data, keep_blank_values=True)
        received_hash = params.get('hash', [''])[0]
        if not received_hash:
            return None

        # Build data-check-string
        check_items = []
        for key in sorted(params.keys()):
            if key != 'hash':
                check_items.append(f"{key}={params[key][0]}")
        data_check_string = '\n'.join(check_items)

        # HMAC-SHA256 validation
        secret_key = hmac.new(b'WebAppData', TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("Mini App initData hash mismatch")
            return None

        # Extract user_id
        user_json = params.get('user', [''])[0]
        if user_json:
            user_data = json.loads(user_json)
            return user_data.get('id')

        return None
    except Exception as e:
        logger.warning(f"initData validation error: {e}")
        return None


def _handle_signed_url_request(body: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """Generate OSS PUT signed URL for direct client upload."""
    cors_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }

    try:
        # Validate initData
        init_data = body.get('init_data', '')
        user_id = _validate_init_data(init_data)
        if not user_id:
            return {'statusCode': 403, 'headers': cors_headers,
                    'body': json.dumps({'error': 'Invalid authentication'})}

        ext = body.get('ext', '.mp3').lower()
        allowed_ext = ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac',
                       '.mp4', '.mov', '.mkv', '.webm']
        if ext not in allowed_ext:
            return {'statusCode': 400, 'headers': cors_headers,
                    'body': json.dumps({'error': f'Unsupported format: {ext}'})}

        # Generate OSS key and signed PUT URL
        import uuid
        oss_key = f"uploads/{user_id}/{uuid.uuid4().hex}{ext}"

        import oss2
        ak = ALIBABA_ACCESS_KEY or os.environ.get('ALIBABA_ACCESS_KEY')
        sk = ALIBABA_SECRET_KEY or os.environ.get('ALIBABA_SECRET_KEY')
        st = ALIBABA_SECURITY_TOKEN or os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN')
        oss_endpoint = os.environ.get('OSS_ENDPOINT', 'oss-eu-central-1.aliyuncs.com')
        oss_bucket_name = os.environ.get('OSS_BUCKET', 'twbot-prod-audio')

        if not oss_endpoint.startswith('http'):
            oss_endpoint = f'https://{oss_endpoint}'

        if st:
            auth = oss2.StsAuth(ak, sk, st)
        else:
            auth = oss2.Auth(ak, sk)
        bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket_name)

        # 15-minute expiry for PUT URL (minimize exposure window)
        put_url = bucket.sign_url('PUT', oss_key, 900,
                                   headers={'Content-Type': 'application/octet-stream'})

        logger.info(f"[upload] signed URL generated for user {user_id}, key={oss_key}")
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': json.dumps({'put_url': put_url, 'oss_key': oss_key})
        }

    except Exception as e:
        logger.error(f"Signed URL generation error: {e}", exc_info=True)
        return {'statusCode': 500, 'headers': cors_headers,
                'body': json.dumps({'error': 'Internal error'})}


def _handle_process_upload(body: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """Create processing job from uploaded OSS file."""
    cors_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
    }

    try:
        # Validate initData
        init_data = body.get('init_data', '')
        user_id = _validate_init_data(init_data)
        if not user_id:
            return {'statusCode': 403, 'headers': cors_headers,
                    'body': json.dumps({'error': 'Invalid authentication'})}

        oss_key = body.get('oss_key', '')
        if not oss_key or not oss_key.startswith(f'uploads/{user_id}/'):
            return {'statusCode': 400, 'headers': cors_headers,
                    'body': json.dumps({'error': 'Invalid OSS key'})}

        filename = body.get('filename', 'upload')

        # Create job in Tablestore
        import uuid
        job_id = str(uuid.uuid4())
        db = get_db_service()
        tg = get_telegram_service()

        # Send status message to user
        status_msg = tg.send_message(
            user_id,
            f"📎 Файл <b>{filename}</b> получен.\n🔄 Начинаю обработку...",
            parse_mode='HTML'
        )
        status_message_id = status_msg['result']['message_id'] if status_msg and status_msg.get('ok') else None

        # Create job record
        db.create_job({
            'job_id': job_id,
            'user_id': str(user_id),
            'chat_id': str(user_id),
            'file_id': oss_key,  # OSS key instead of Telegram file_id
            'file_type': 'oss_upload',
            'duration': 0,  # Will be detected by audio-processor
            'status': 'pending',
            'status_message_id': str(status_message_id) if status_message_id else '',
        })

        # Queue for async processing via MNS
        job_data = {
            'job_id': job_id,
            'user_id': str(user_id),
            'chat_id': str(user_id),
            'file_id': oss_key,
            'file_type': 'oss_upload',
            'duration': 0,
            'status_message_id': str(status_message_id) if status_message_id else '',
        }

        from services.mns_service import MNSService
        if MNS_ENDPOINT and ALIBABA_ACCESS_KEY and ALIBABA_SECRET_KEY:
            mns = MNSService(
                endpoint=MNS_ENDPOINT,
                access_key_id=ALIBABA_ACCESS_KEY,
                access_key_secret=ALIBABA_SECRET_KEY,
                queue_name=os.environ.get('AUDIO_JOBS_QUEUE',
                                           'telegram-whisper-bot-prod-audio-jobs')
            )
            from services.mns_service import MNSPublisher
            publisher = MNSPublisher(mns)
            published = publisher.publish(job_data)
            if not published:
                db.update_job(job_id, {'status': 'failed', 'error': 'MNS publish failed'})
                return {'statusCode': 500, 'headers': cors_headers,
                        'body': json.dumps({'error': 'Queue error'})}
        else:
            logger.warning("MNS not configured, cannot process upload async")
            db.update_job(job_id, {'status': 'failed', 'error': 'MNS not configured'})
            return {'statusCode': 500, 'headers': cors_headers,
                    'body': json.dumps({'error': 'Async processing unavailable'})}

        logger.info(f"[upload] job {job_id} created for user {user_id}, oss_key={oss_key}")
        return {
            'statusCode': 200,
            'headers': cors_headers,
            'body': json.dumps({'ok': True, 'job_id': job_id})
        }

    except Exception as e:
        logger.error(f"Process upload error: {e}", exc_info=True)
        return {'statusCode': 500, 'headers': cors_headers,
                'body': json.dumps({'error': 'Internal error'})}
