"""
Audio Processor for Alibaba Cloud Function Compute
Processes audio from MNS queue and transcribes using Qwen-ASR (Paraformer)
"""
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

# Add services to path
sys.path.insert(0, os.path.dirname(__file__))

# Configure structured JSON logging for SLS
from services.utility import UtilityService
UtilityService.setup_logging(
    'audio-processor',
    bot_token=os.environ.get('TELEGRAM_BOT_TOKEN'),
    owner_id=os.environ.get('OWNER_ID'),
)
logger = logging.getLogger(__name__)

# Environment variables
TABLESTORE_ENDPOINT = os.environ.get('TABLESTORE_ENDPOINT', 'https://twbot-prod.eu-central-1.ots.aliyuncs.com')
TABLESTORE_INSTANCE = os.environ.get('TABLESTORE_INSTANCE', 'twbot-prod')
MNS_ENDPOINT = os.environ.get('MNS_ENDPOINT')
REGION = os.environ.get('REGION', 'eu-central-1')
WHISPER_BACKEND = os.environ.get('WHISPER_BACKEND', 'qwen-asr')

# Credentials - FC provides STS credentials automatically
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
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')

# Global service instances
_db_service = None
_telegram_service = None
_audio_service = None


def get_db_service():
    """Get or create Tablestore service instance."""
    global _db_service
    if _db_service is None:
        if not ALIBABA_ACCESS_KEY or not ALIBABA_SECRET_KEY:
            raise ValueError("Alibaba credentials not configured")
        from services.tablestore_service import TablestoreService
        _db_service = TablestoreService(
            endpoint=TABLESTORE_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            instance_name=TABLESTORE_INSTANCE,
            security_token=ALIBABA_SECURITY_TOKEN
        )
    return _db_service


def get_telegram_service():
    """Get or create Telegram service instance."""
    global _telegram_service
    if _telegram_service is None:
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN not configured")
        from services.telegram import TelegramService
        _telegram_service = TelegramService(TELEGRAM_BOT_TOKEN)
    return _telegram_service


def get_audio_service():
    """Get or create Audio service instance."""
    global _audio_service
    if _audio_service is None:
        if not DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY not configured")
        from services.audio import AudioService
        _audio_service = AudioService(
            whisper_backend=WHISPER_BACKEND,
            alibaba_api_key=DASHSCOPE_API_KEY,
            oss_config={
                'bucket': os.environ.get('OSS_BUCKET', 'twbot-prod-audio'),
                'endpoint': os.environ.get('OSS_ENDPOINT', 'oss-eu-central-1.aliyuncs.com'),
                'access_key_id': ALIBABA_ACCESS_KEY,
                'access_key_secret': ALIBABA_SECRET_KEY,
                'security_token': ALIBABA_SECURITY_TOKEN,
            }
        )
    return _audio_service


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    FC handler for audio processing
    Triggered by timer or MNS message
    """
    try:
        # Parse event - can be bytes, str, or dict
        if isinstance(event, bytes):
            event = json.loads(event.decode('utf-8'))
        elif isinstance(event, str):
            event = json.loads(event)

        logger.info(f"Audio processor triggered, event type: {type(event)}")

        # Check if this is a timer trigger (polling mode)
        # Timer trigger format: {'triggerTime': '...', 'triggerName': '...', 'payload': '...'}
        if 'triggerName' in event:
            payload = event.get('payload', '{}')
            if isinstance(payload, str):
                payload = json.loads(payload)
            if payload.get('action') == 'poll_queue':
                return poll_queue()
            event = payload  # Use payload for further processing

        if event.get('action') == 'poll_queue':
            return poll_queue()

        # Check if this is an MNS message
        if 'Message' in event or 'job_id' in event:
            return process_mns_message(event)

        # Direct invocation with job data
        if 'body' in event:
            body = event.get('body', '{}')
            if isinstance(body, str):
                body = json.loads(body)
            return process_job(body)

        logger.warning(f"Unknown event format: {event}")
        return {'statusCode': 200, 'body': 'Unknown event format'}

    except Exception as e:
        logger.error(f"Error in audio processor: {e}", exc_info=True)
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def poll_queue() -> Dict[str, Any]:
    """Poll MNS queue for messages and process them."""
    from services.mns_service import MNSService

    try:
        if not MNS_ENDPOINT or not ALIBABA_ACCESS_KEY or not ALIBABA_SECRET_KEY:
            raise ValueError("MNS configuration incomplete")
        mns = MNSService(
            endpoint=MNS_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            queue_name=os.environ.get('AUDIO_JOBS_QUEUE', 'telegram-whisper-bot-prod-audio-jobs')
        )

        # Receive messages (batch of up to 10)
        messages = mns.receive_messages(batch_size=10, wait_seconds=1)

        if not messages:
            return {'statusCode': 200, 'body': 'No messages in queue'}

        processed = 0
        for msg in messages:
            try:
                job_data = json.loads(msg.get('body', '{}'))
                result = process_job(job_data)

                if result.get('ok', False):
                    mns.delete_message(msg.get('receipt_handle'))
                    processed += 1

            except Exception as e:
                logger.error(f"Error processing message: {e}")

        return {'statusCode': 200, 'body': f'Processed {processed}/{len(messages)} messages'}

    except Exception as e:
        logger.error(f"Error polling queue: {e}")
        return {'statusCode': 500, 'body': str(e)}


def process_mns_message(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single MNS message."""
    # Extract job data from MNS message format
    if 'Message' in event:
        message = event['Message']
        if isinstance(message, str):
            job_data = json.loads(message)
        else:
            job_data = message
    else:
        job_data = event

    return process_job(job_data)


def process_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single audio processing job."""
    job_id = job_data.get('job_id')
    user_id = job_data.get('user_id')
    chat_id = job_data.get('chat_id')
    file_id = job_data.get('file_id')
    _file_type = job_data.get('file_type', 'voice')  # Preserved for future use
    duration = job_data.get('duration', 0)

    if job_id is None or user_id is None or chat_id is None or file_id is None:
        logger.error(f"Invalid job data: {job_data}")
        return {'ok': False, 'error': 'Missing required fields'}

    # Type narrowing after validation - reassign with correct types
    job_id = str(job_id)
    user_id_int = int(user_id)  # For numeric operations (balance, settings)
    user_id = str(user_id)  # Keep as string for job records
    chat_id = int(chat_id)
    file_id = str(file_id)

    # Reuse status message from webhook-handler if available
    status_message_id = job_data.get('status_message_id')
    if status_message_id:
        status_message_id = int(status_message_id)

    logger.info(f"Processing job {job_id} for user {user_id}")

    db = get_db_service()
    tg = get_telegram_service()
    audio = get_audio_service()

    try:
        # Update job status
        db.update_job(job_id, {'status': 'processing'})

        # Use existing status message or create new one for progress
        if status_message_id:
            progress_id = status_message_id
            tg.edit_message_text(chat_id, progress_id, "🔄 Обработка началась...")
        else:
            progress_msg = tg.send_message(chat_id, "🔄 Обработка началась...")
            progress_id = progress_msg['result']['message_id'] if progress_msg and progress_msg.get('ok') else None

        # Download file from Telegram
        if progress_id:
            tg.edit_message_text(chat_id, progress_id, "📥 Загружаю файл...")
        tg.send_chat_action(chat_id, 'typing')

        telegram_file_path = tg.get_file_path(file_id)
        if not telegram_file_path:
            raise Exception("Failed to get file path from Telegram")

        local_path = tg.download_file(telegram_file_path)
        if not local_path:
            raise Exception("Failed to download file from Telegram")

        # Update progress: transcribing
        if progress_id:
            tg.edit_message_text(chat_id, progress_id, "🎙 Распознаю речь...")
        tg.send_chat_action(chat_id, 'typing')

        # Convert audio (with video detection)
        converted_path = audio.prepare_audio_for_asr(local_path)
        if not converted_path:
            raise Exception("Failed to convert audio to MP3")

        # Extract settings from user
        user = db.get_user(user_id_int)
        settings_json = user.get('settings', '{}') if user else '{}'
        settings = json.loads(settings_json) if isinstance(settings_json, str) else (settings_json or {})
        use_code = settings.get('use_code_tags', False)
        use_yo = settings.get('use_yo', True)
        dialogue_mode = settings.get('dialogue_mode', False)

        is_dialogue = False

        if dialogue_mode:
            # Diarization path (Fun-ASR, async, OSS)
            raw_text, segments = audio.transcribe_with_diarization(
                converted_path,
                progress_callback=lambda stage: (
                    tg.edit_message_text(chat_id, progress_id, stage)
                    if progress_id else None
                )
            )
            if segments:
                text = audio.format_dialogue(segments)
                is_dialogue = True
            else:
                # Fallback: regular transcription
                def chunk_progress(current, total):
                    if progress_id and total > 1:
                        tg.edit_message_text(chat_id, progress_id,
                            f"🎙 Распознаю речь... (часть {current} из {total})")

                text = raw_text or audio.transcribe_audio(
                    converted_path, progress_callback=chunk_progress)
        else:
            # Regular path (Qwen3-ASR)
            def chunk_progress(current, total):
                if progress_id and total > 1:
                    tg.edit_message_text(chat_id, progress_id,
                        f"🎙 Распознаю речь... (часть {current} из {total})")

            text = audio.transcribe_audio(converted_path, progress_callback=chunk_progress)

        # Send diarization debug info to admin
        if dialogue_mode:
            owner_id = int(os.environ.get('OWNER_ID', 0))
            if owner_id and chat_id == owner_id:
                debug_text = audio.get_diarization_debug()
                if debug_text:
                    tg.send_message(owner_id, f"<pre>{debug_text}</pre>", parse_mode='HTML')

        if not text or text.strip() == "" or text.strip() == "Продолжение следует...":
            tg.send_message(chat_id, "На записи не обнаружено речи или текст не был распознан.")
            if progress_id:
                tg.delete_message(chat_id, progress_id)
            db.update_job(job_id, {'status': 'failed', 'error': 'no_speech'})
            return {'ok': True, 'result': 'no_speech'}

        # Determine if audio was chunked (for LLM prompt)
        audio_duration = audio.get_audio_duration(converted_path)
        is_chunked = audio_duration > audio.ASR_MAX_CHUNK_DURATION

        # Format text (Qwen LLM with Gemini fallback)
        if len(text) > 100:
            if progress_id:
                tg.edit_message_text(chat_id, progress_id, "✏️ Форматирую текст...")
            tg.send_chat_action(chat_id, 'typing')
            formatted_text = audio.format_text_with_qwen(
                text, use_code_tags=use_code, use_yo=use_yo,
                is_chunked=is_chunked, is_dialogue=is_dialogue)
        else:
            formatted_text = text
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

        # Send result: edit progress message or send new one
        if use_code:
            result_text = f"<code>{formatted_text}</code>"
            parse_mode = 'HTML'
        else:
            result_text = formatted_text
            parse_mode = ''

        long_text_mode = settings.get('long_text_mode', 'split')

        if progress_id and len(result_text) <= 4000:
            tg.edit_message_text(chat_id, progress_id, result_text, parse_mode=parse_mode)
        elif long_text_mode == 'file':
            if progress_id:
                tg.delete_message(chat_id, progress_id)
            first_dot = formatted_text.find('.')
            caption = (formatted_text[:first_dot+1] if 0 < first_dot < 200 else formatted_text[:200]) + "..."
            tg.send_as_file(chat_id, formatted_text, caption=caption)
        else:
            if progress_id:
                tg.delete_message(chat_id, progress_id)
            tg.send_long_message(chat_id, result_text, parse_mode=parse_mode)

        # Update balance
        duration_minutes = (duration + 59) // 60
        balance = user.get('balance_minutes', 0) if user else 0
        balance_updated = db.update_user_balance(user_id_int, -duration_minutes)
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
                        f"Job: {job_id}\n"
                        f"Требуется ручная корректировка."
                    )
            except Exception as notify_err:
                logger.error(f"Failed to notify owner about balance error: {notify_err}")

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

        # Log and update job status (after sending result to reduce perceived latency)
        db.log_transcription({
            'user_id': user_id,
            'duration': duration,
            'char_count': len(formatted_text),
            'status': 'completed'
        })

        db.update_job(job_id, {
            'status': 'completed',
            'result': json.dumps({'text_length': len(formatted_text)})
        })

        # Cleanup
        try:
            os.remove(local_path)
            if converted_path != local_path:
                os.remove(converted_path)
        except OSError as cleanup_err:
            logger.debug(f"Cleanup temp files failed: {cleanup_err}")

        logger.info(f"Job {job_id} completed successfully")
        return {'ok': True, 'result': 'completed'}

    except Exception as e:
        logger.error(f"Error processing job {job_id}: {e}", exc_info=True)

        # Update job status
        db.update_job(job_id, {'status': 'failed', 'error': str(e)[:200]})

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

        return {'ok': False, 'error': str(e)}
