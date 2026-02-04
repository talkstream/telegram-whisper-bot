"""
Telegram Webhook Handler for Alibaba Cloud Function Compute
Full implementation with Tablestore, MNS, and Qwen-ASR
"""
import json
import logging
import os
import sys
import asyncio
from typing import Any, Dict, Optional

# Add shared services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'telegram_bot_shared'))

# Configure logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'WARNING')
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
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
SYNC_PROCESSING_THRESHOLD = 30

# Global service instances (lazy initialization)
_db_service = None
_telegram_service = None


def get_db_service(access_key_id=None, access_key_secret=None, security_token=None):
    """Get or create Tablestore service instance."""
    global _db_service
    if _db_service is None:
        from services.tablestore_service import TablestoreService
        _db_service = TablestoreService(
            endpoint=TABLESTORE_ENDPOINT,
            access_key_id=access_key_id or ALIBABA_ACCESS_KEY,
            access_key_secret=access_key_secret or ALIBABA_SECRET_KEY,
            security_token=security_token or ALIBABA_SECURITY_TOKEN,
            instance_name=TABLESTORE_INSTANCE
        )
    return _db_service


def get_telegram_service():
    """Get or create Telegram service instance."""
    global _telegram_service
    if _telegram_service is None:
        from services.telegram import TelegramService
        _telegram_service = TelegramService(TELEGRAM_BOT_TOKEN)
    return _telegram_service


def create_http_response(status_code: int, body: Dict[str, Any]) -> str:
    """
    Create HTTP response for FC HTTP Trigger.
    For anonymous HTTP triggers, return JSON string directly.
    """
    # For FC HTTP triggers, return just the JSON body as string
    return json.dumps(body, ensure_ascii=False)


def handler(environ, context):
    """
    FC HTTP trigger handler for Telegram webhook.
    Receives WSGI environ dict for HTTP triggers.
    """
    try:
        # For FC HTTP trigger, environ is a WSGI environment dict
        # Extract credentials from environ (FC provides STS credentials)
        global ALIBABA_ACCESS_KEY, ALIBABA_SECRET_KEY, ALIBABA_SECURITY_TOKEN, _db_service, _telegram_service

        # Always reset services to ensure fresh credentials and tokens
        _db_service = None
        _telegram_service = None

        if isinstance(environ, dict):
            new_access_key = environ.get('accessKeyID') or environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
            if new_access_key:
                ALIBABA_ACCESS_KEY = new_access_key
                ALIBABA_SECRET_KEY = environ.get('accessKeySecret') or environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
                ALIBABA_SECURITY_TOKEN = environ.get('securityToken') or environ.get('ALIBABA_CLOUD_SECURITY_TOKEN')

        # Get HTTP method
        http_method = environ.get('REQUEST_METHOD', 'POST')
        logger.info(f"HTTP method: {http_method}")

        # Handle health check (GET request)
        if http_method.upper() == 'GET':
            return create_http_response(200, {
                'status': 'ok',
                'service': 'telegram-whisper-bot',
                'region': REGION,
                'version': '3.0.0-alibaba',
                'telegram_token_set': bool(TELEGRAM_BOT_TOKEN),
                'telegram_token_len': len(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else 0
            })

        # Read body from wsgi.input
        request_body = {}
        wsgi_input = environ.get('wsgi.input')
        if wsgi_input:
            try:
                content_length = int(environ.get('CONTENT_LENGTH', 0))
                if content_length > 0:
                    body_bytes = wsgi_input.read(content_length)
                    if body_bytes:
                        request_body = json.loads(body_bytes.decode('utf-8'))
            except Exception as e:
                logger.warning(f"Failed to read body: {e}")

        logger.info(f"Processing update: {str(request_body)[:200]}")

        result = process_update(request_body)

        return create_http_response(200, {'ok': True, 'result': result})

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return create_http_response(500, {'ok': False, 'error': str(e)})


def process_update(update: Dict[str, Any]) -> str:
    """Process a Telegram update."""
    # Handle callback query
    if 'callback_query' in update:
        return handle_callback_query(update['callback_query'])

    # Handle message
    if 'message' in update:
        return handle_message(update['message'])

    # Handle pre_checkout_query (payments)
    if 'pre_checkout_query' in update:
        return handle_pre_checkout(update['pre_checkout_query'])

    # Handle successful_payment
    if 'successful_payment' in update.get('message', {}):
        return handle_successful_payment(update['message'])

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
        # Create new user
        user_data = {
            'first_name': message.get('from', {}).get('first_name', ''),
            'last_name': message.get('from', {}).get('last_name', ''),
            'username': message.get('from', {}).get('username', ''),
            'balance_minutes': 0,
            'trial_status': 'none',
            'settings': json.dumps({'use_code_tags': False, 'use_yo': True})
        }
        db.create_user(user_id, user_data)
        user = db.get_user(user_id)

    # Check for audio/voice/video
    if any(key in message for key in ['voice', 'audio', 'video', 'video_note']):
        return handle_audio_message(message, user)

    # Check for commands
    if text.startswith('/'):
        return handle_command(message, user)

    return 'message_received'


def handle_audio_message(message: Dict[str, Any], user: Dict[str, Any]) -> str:
    """Handle audio/voice/video message."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')

    tg = get_telegram_service()
    db = get_db_service()

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
    else:
        return 'no_audio'

    # Check user balance
    balance = user.get('balance_minutes', 0)
    duration_minutes = (duration + 59) // 60  # Round up

    if balance < duration_minutes:
        # Check trial status
        trial_status = user.get('trial_status', 'none')
        if trial_status == 'none':
            tg.send_message(
                chat_id,
                "У вас недостаточно минут для транскрипции.\n"
                "Используйте /trial для запроса пробного доступа или /buy_minutes для покупки."
            )
            return 'insufficient_balance'

    # Send processing notification
    tg.send_message(chat_id, "🎙 Аудио получено. Обрабатываю...")

    # For short audio (< 30 sec), process synchronously
    if duration < SYNC_PROCESSING_THRESHOLD:
        return process_audio_sync(message, user, file_id, file_type, duration)
    else:
        return queue_audio_async(message, user, file_id, file_type, duration)


def process_audio_sync(message: Dict[str, Any], user: Dict[str, Any],
                       file_id: str, file_type: str, duration: int) -> str:
    """Process audio synchronously (for short files)."""
    import tempfile
    import uuid

    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')

    tg = get_telegram_service()
    db = get_db_service()

    try:
        # Download file from Telegram
        telegram_file_path = tg.get_file_path(file_id)
        if not telegram_file_path:
            tg.send_message(chat_id, "Не удалось получить файл. Попробуйте ещё раз.")
            return 'download_failed'

        # download_file returns the local path where file was saved
        local_path = tg.download_file(telegram_file_path)
        if not local_path:
            tg.send_message(chat_id, "Не удалось скачать файл. Попробуйте ещё раз.")
            return 'download_failed'

        # Transcribe with Qwen-ASR
        from services.audio import AudioService
        audio_service = AudioService(whisper_backend='qwen-asr')

        # Convert and transcribe
        converted_path = audio_service.convert_to_mp3(local_path)
        if not converted_path:
            tg.send_message(chat_id, "Не удалось обработать аудио. Попробуйте другой формат.")
            return 'conversion_failed'

        text = audio_service.transcribe_audio(converted_path)

        if not text or text.strip() == "Продолжение следует...":
            tg.send_message(chat_id, "На записи не обнаружено речи или текст не был распознан.")
            return 'no_speech'

        # Get user settings BEFORE formatting
        settings = db.get_user_settings(user_id) or {}
        use_code_tags = settings.get('use_code_tags', False)
        use_yo = settings.get('use_yo', True)

        # Format text with Qwen LLM (with Gemini fallback)
        if len(text) > 50:
            formatted_text = audio_service.format_text_with_qwen(text, use_code_tags=use_code_tags, use_yo=use_yo)
        else:
            formatted_text = text
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

        # Send result
        if use_code_tags:
            tg.send_message(chat_id, f"<code>{formatted_text}</code>", parse_mode='HTML')
        else:
            tg.send_message(chat_id, formatted_text)

        # Update balance
        duration_minutes = (duration + 59) // 60
        db.update_user_balance(user_id, -duration_minutes)

        # Log transcription
        db.log_transcription({
            'user_id': str(user_id),
            'duration': duration,
            'char_count': len(formatted_text),
            'status': 'completed'
        })

        # Cleanup
        try:
            os.remove(local_path)
            if converted_path != local_path:
                os.remove(converted_path)
        except:
            pass

        return 'transcribed_sync'

    except Exception as e:
        logger.error(f"Error in sync processing: {e}", exc_info=True)
        tg.send_message(chat_id, f"Произошла ошибка при обработке: {str(e)[:100]}")
        return 'error'


def queue_audio_async(message: Dict[str, Any], user: Dict[str, Any],
                      file_id: str, file_type: str, duration: int) -> str:
    """Queue audio for async processing via MNS."""
    import uuid

    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    message_id = message.get('message_id')

    db = get_db_service()
    tg = get_telegram_service()

    # Create job
    job_id = str(uuid.uuid4())
    job_data = {
        'job_id': job_id,
        'user_id': str(user_id),
        'chat_id': chat_id,
        'message_id': message_id,
        'file_id': file_id,
        'file_type': file_type,
        'duration': duration,
        'status': 'pending'
    }

    db.create_job(job_data)

    # Publish to MNS queue
    try:
        from services.mns_service import MNSPublisher
        publisher = MNSPublisher(
            endpoint=MNS_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            queue_name=AUDIO_JOBS_QUEUE
        )
        publisher.publish(job_data)
        logger.info(f"Published job {job_id} to MNS queue")
    except Exception as e:
        logger.error(f"Failed to publish to MNS: {e}")
        # Fallback: process sync even if long
        return process_audio_sync(message, user, file_id, file_type, duration)

    tg.send_message(chat_id, "⏳ Аудио в очереди на обработку...")
    return 'queued'


def handle_command(message: Dict[str, Any], user: Dict[str, Any]) -> str:
    """Handle bot commands."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    text = message.get('text', '')
    command = text.split()[0].lower().replace('@', '').split('@')[0]

    tg = get_telegram_service()
    db = get_db_service()

    if command == '/start':
        tg.send_message(
            chat_id,
            "👋 Добро пожаловать в Telegram Whisper Bot!\n\n"
            "Отправьте мне голосовое сообщение или аудиофайл, и я преобразую его в текст.\n\n"
            "Команды:\n"
            "/help - Справка\n"
            "/balance - Проверить баланс\n"
            "/trial - Запросить пробный доступ\n"
            "/settings - Настройки"
        )
        return 'start'

    elif command == '/help':
        tg.send_message(
            chat_id,
            "📖 Справка по боту\n\n"
            "Отправьте голосовое сообщение, аудио или видео для транскрипции.\n\n"
            "Команды:\n"
            "/balance - Проверить остаток минут\n"
            "/trial - Запросить пробный доступ (15 мин)\n"
            "/buy_minutes - Купить минуты\n"
            "/settings - Показать настройки\n"
            "/code - Вкл/выкл моноширинный шрифт\n"
            "/yo - Вкл/выкл букву ё"
        )
        return 'help'

    elif command == '/balance':
        balance = user.get('balance_minutes', 0)
        tg.send_message(chat_id, f"💰 Ваш баланс: {balance} минут")
        return 'balance'

    elif command == '/trial':
        trial_status = user.get('trial_status', 'none')
        if trial_status == 'approved':
            tg.send_message(chat_id, "Вы уже использовали пробный период.")
        elif trial_status == 'pending':
            tg.send_message(chat_id, "Ваш запрос на пробный период уже на рассмотрении.")
        else:
            db.create_trial_request(user_id, {
                'status': 'pending',
                'user_name': user.get('first_name', ''),
                'request_timestamp': 'now'
            })
            db.update_user(user_id, {'trial_status': 'pending'})
            tg.send_message(chat_id, "✅ Запрос на пробный период отправлен. Ожидайте одобрения.")
        return 'trial'

    elif command == '/settings':
        settings = db.get_user_settings(user_id) or {}
        use_code = settings.get('use_code_tags', False)
        use_yo = settings.get('use_yo', True)

        tg.send_message(
            chat_id,
            f"⚙️ Ваши настройки:\n\n"
            f"Моноширинный шрифт: {'✅ Вкл' if use_code else '❌ Выкл'}\n"
            f"Использование ё: {'✅ Вкл' if use_yo else '❌ Выкл (заменяется на е)'}\n\n"
            f"Команды для изменения:\n"
            f"/code - переключить шрифт\n"
            f"/yo - переключить букву ё"
        )
        return 'settings'

    elif command == '/code':
        settings = db.get_user_settings(user_id) or {}
        settings['use_code_tags'] = not settings.get('use_code_tags', False)
        db.update_user_settings(user_id, settings)
        status = 'включено' if settings['use_code_tags'] else 'выключено'
        tg.send_message(chat_id, f"Моноширинный шрифт: {status}")
        return 'code_toggle'

    elif command == '/yo':
        settings = db.get_user_settings(user_id) or {}
        settings['use_yo'] = not settings.get('use_yo', True)
        db.update_user_settings(user_id, settings)
        status = 'включено' if settings['use_yo'] else 'замена на е'
        tg.send_message(chat_id, f"Использование буквы ё: {status}")
        return 'yo_toggle'

    else:
        tg.send_message(chat_id, "Неизвестная команда. Используйте /help для справки.")
        return 'unknown_command'


def handle_callback_query(callback_query: Dict[str, Any]) -> str:
    """Handle callback query from inline keyboard."""
    callback_data = callback_query.get('data', '')
    user_id = callback_query.get('from', {}).get('id')

    logger.info(f"Callback query from {user_id}: {callback_data}")
    # TODO: Implement callback handlers for payments, trial approvals, etc.
    return f'callback_{callback_data}'


def handle_pre_checkout(pre_checkout_query: Dict[str, Any]) -> str:
    """Handle pre-checkout query for Telegram Payments."""
    tg = get_telegram_service()
    query_id = pre_checkout_query.get('id')
    tg.answer_pre_checkout_query(query_id, ok=True)
    return 'pre_checkout_approved'


def handle_successful_payment(message: Dict[str, Any]) -> str:
    """Handle successful payment."""
    chat_id = message.get('chat', {}).get('id')
    user_id = message.get('from', {}).get('id')
    payment = message.get('successful_payment', {})

    tg = get_telegram_service()
    db = get_db_service()

    # Parse invoice payload
    payload = payment.get('invoice_payload', '')
    # Payload format: "minutes_XXX" where XXX is the number of minutes

    try:
        minutes = int(payload.split('_')[1])
    except:
        minutes = 0

    if minutes > 0:
        db.update_user_balance(user_id, minutes)
        db.log_payment({
            'user_id': str(user_id),
            'minutes_added': minutes,
            'stars_amount': payment.get('total_amount', 0),
            'telegram_payment_charge_id': payment.get('telegram_payment_charge_id', '')
        })
        tg.send_message(chat_id, f"✅ Оплата прошла успешно! Добавлено {minutes} минут.")

    return 'payment_processed'
