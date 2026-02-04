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
        from services.telegram import TelegramService
        _telegram_service = TelegramService(TELEGRAM_BOT_TOKEN)
    return _telegram_service


def get_audio_service():
    """Get or create Audio service instance."""
    global _audio_service
    if _audio_service is None:
        from services.audio import AudioService
        _audio_service = AudioService(
            whisper_backend=WHISPER_BACKEND,
            alibaba_api_key=DASHSCOPE_API_KEY
        )
    return _audio_service


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
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
    file_type = job_data.get('file_type', 'voice')
    duration = job_data.get('duration', 0)

    if not all([job_id, user_id, chat_id, file_id]):
        logger.error(f"Invalid job data: {job_data}")
        return {'ok': False, 'error': 'Missing required fields'}

    logger.info(f"Processing job {job_id} for user {user_id}")

    db = get_db_service()
    tg = get_telegram_service()
    audio = get_audio_service()

    try:
        # Update job status
        db.update_job(job_id, {'status': 'processing'})

        # Download file from Telegram
        telegram_file_path = tg.get_file_path(file_id)
        if not telegram_file_path:
            raise Exception("Failed to get file path from Telegram")

        local_path = tg.download_file(telegram_file_path)
        if not local_path:
            raise Exception("Failed to download file from Telegram")

        # Send progress message
        tg.send_message(chat_id, "🔄 Транскрибирую...")

        # Convert audio
        converted_path = audio.convert_to_mp3(local_path)
        if not converted_path:
            raise Exception("Failed to convert audio to MP3")

        # Transcribe
        text = audio.transcribe_audio(converted_path)

        if not text or text.strip() == "":
            tg.send_message(chat_id, "На записи не обнаружено речи или текст не был распознан.")
            db.update_job(job_id, {'status': 'failed', 'error': 'no_speech'})
            return {'ok': True, 'result': 'no_speech'}

        # Check for "continuation follows" phrase (indicates no speech detected)
        if text.strip() == "Продолжение следует...":
            tg.send_message(chat_id, "На записи не обнаружено речи или текст не был распознан.")
            db.update_job(job_id, {'status': 'failed', 'error': 'no_speech'})
            return {'ok': True, 'result': 'no_speech'}

        # Get user settings BEFORE formatting
        settings = db.get_user_settings(int(user_id)) or {}
        use_code = settings.get('use_code_tags', False)
        use_yo = settings.get('use_yo', True)

        # Format text (Qwen LLM with Gemini fallback)
        if len(text) > 50:
            formatted_text = audio.format_text_with_qwen(text, use_code_tags=use_code, use_yo=use_yo)
        else:
            formatted_text = text
            # Apply yo setting for short text that wasn't formatted
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

        # Send result
        if use_code:
            tg.send_message(chat_id, f"<code>{formatted_text}</code>", parse_mode='HTML')
        else:
            tg.send_message(chat_id, formatted_text)

        # Update balance
        duration_minutes = (duration + 59) // 60
        db.update_user_balance(int(user_id), -duration_minutes)

        # Log transcription
        db.log_transcription({
            'user_id': user_id,
            'duration': duration,
            'char_count': len(formatted_text),
            'status': 'completed'
        })

        # Update job status
        db.update_job(job_id, {
            'status': 'completed',
            'result': json.dumps({'text_length': len(formatted_text)})
        })

        # Cleanup
        try:
            os.remove(local_path)
            if converted_path != local_path:
                os.remove(converted_path)
        except:
            pass

        logger.info(f"Job {job_id} completed successfully")
        return {'ok': True, 'result': 'completed'}

    except Exception as e:
        logger.error(f"Error processing job {job_id}: {e}", exc_info=True)

        # Update job status
        db.update_job(job_id, {'status': 'failed', 'error': str(e)[:200]})

        # Notify user
        tg.send_message(chat_id, f"Произошла ошибка при обработке аудио. Попробуйте позже.")

        return {'ok': False, 'error': str(e)}
