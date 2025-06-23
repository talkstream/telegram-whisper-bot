import os
import json
import base64
import logging
import tempfile
import subprocess
import gc
from datetime import datetime
from typing import Dict, Any, Optional

# Import firestore for SERVER_TIMESTAMP
from google.cloud import firestore

# Optional memory monitoring
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Configure logging first
logging.basicConfig(level=logging.INFO)

# Constants
PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')
DATABASE_ID = 'editorials-robot'
LOCATION = 'europe-west1'

# Global services - initialized once per instance
_services_initialized = False
_telegram_service = None
_openai_client = None
_db_client = None
_firestore_service = None
_audio_service = None
_secret_manager = None

def initialize_services():
    """Initialize services once per Cloud Function instance"""
    global _services_initialized, _telegram_service, _openai_client, _db_client, _firestore_service, _audio_service, _secret_manager
    
    if _services_initialized:
        return _telegram_service, _openai_client, _db_client, _firestore_service
    
    logging.info("Initializing services for this instance...")
    
    # Import heavy libraries only when needed
    from google.cloud import firestore as fs
    from google.cloud import secretmanager
    from openai import OpenAI
    import vertexai
    from services.telegram import TelegramService
    from services.firestore import FirestoreService
    
    # Initialize secret manager
    _secret_manager = secretmanager.SecretManagerServiceClient()
    
    # Load secrets
    def get_secret(secret_id):
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
        response = _secret_manager.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    
    telegram_bot_token = get_secret("telegram-bot-token")
    openai_api_key = get_secret("openai-api-key")
    
    # Initialize services
    _telegram_service = TelegramService(telegram_bot_token)
    _openai_client = OpenAI(api_key=openai_api_key)
    _db_client = fs.Client(project=PROJECT_ID, database=DATABASE_ID)
    _firestore_service = FirestoreService(PROJECT_ID, DATABASE_ID)
    
    # Initialize Vertex AI
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    
    _services_initialized = True
    logging.info("Services initialized successfully")
    
    return _telegram_service, _openai_client, _db_client, _firestore_service, _audio_service

class AudioProcessor:
    def __init__(self, telegram_service, openai_client, db_client, firestore_service=None, audio_service=None):
        """Initialize with pre-configured services"""
        self.telegram = telegram_service
        self.openai_client = openai_client
        self.db = db_client
        self.firestore_service = firestore_service
        self.audio_service = audio_service
        
    def update_job_status(self, job_id: str, status: str, progress: str = None, error: str = None, result: Dict = None):
        """Update job status in Firestore"""
        update_data = {'status': status}
        if progress:
            update_data['progress'] = progress
        if error:
            update_data['error'] = error
        if result:
            update_data['result'] = result
            
        if self.firestore_service:
            self.firestore_service.update_audio_job(job_id, update_data)
        else:
            doc_ref = self.db.collection('audio_jobs').document(job_id)
            update_data['updated_at'] = firestore.SERVER_TIMESTAMP
            doc_ref.update(update_data)
            
        logging.info(f"Updated job {job_id}: status={status}, progress={progress}")
        
        
    def transcribe_audio(self, audio_path: str) -> Optional[str]:
        """Transcribe audio using OpenAI Whisper"""
        if self.audio_service:
            return self.audio_service.transcribe_audio(audio_path)
        # Fallback to legacy implementation
        try:
            with open(audio_path, "rb") as audio_file:
                file_tuple = (os.path.basename(audio_path), audio_file)
                transcription = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_tuple,
                    language="ru",
                    response_format="json"
                )
            return transcription.text
        except Exception as e:
            logging.error(f"Error during transcription: {e}")
            return None
            
    def format_text_with_gemini(self, text: str) -> str:
        """Format text using Gemini AI"""
        if self.audio_service:
            return self.audio_service.format_text_with_gemini(text)
        # Fallback to legacy implementation
        try:
            # Lazy import to save memory
            from vertexai.generative_models import GenerativeModel
            
            model = GenerativeModel("gemini-2.5-flash")
            prompt = f"""
            Твоя задача — отформатировать следующий транскрипт устной речи, улучшив его читаемость, но полностью сохранив исходный смысл, стиль и лексику автора.
            1.  **Формирование абзацев:** Объединяй несколько (обычно от 2 до 5) связанных по теме предложений в один абзац. Начинай новый абзац только при явной смене микро-темы или при переходе к новому аргументу в рассуждении. Избегай создания слишком коротких абзацев из одного предложения.
            2.  **Обработка предложений:** Сохраняй оригинальную структуру предложений. Вмешивайся и разбивай предложение на несколько частей только в тех случаях, когда оно становится **аномально длинным и громоздким** для чтения из-за обилия придаточных частей или перечислений.
            3.  **Строгое сохранение контента:** Категорически запрещено изменять слова, добавлять что-либо от себя или делать выводы. Твоя работа — это работа редактора-форматировщика, а не копирайтера. Сохрани исходный текст в максимальной близости к оригиналу, изменив только разбивку на абзацы и, в редких случаях, структуру самых длинных предложений.
            Исходный текст для обработки:
            ---
            {text}
            ---
            """
            response = model.generate_content(prompt)
            result = response.text
            
            # Clean up prompt to free memory
            del prompt
            del response
            gc.collect()
            
            return result
        except Exception as e:
            logging.error(f"Error calling Gemini API: {e}")
            return text
            
    def process_audio_job(self, job_data: Dict[str, Any]):
        """Main processing function for audio jobs"""
        job_id = job_data['job_id']
        user_id = job_data['user_id']
        chat_id = job_data['chat_id']
        file_id = job_data['file_id']
        file_size = job_data['file_size']
        duration = job_data['duration']
        user_name = job_data['user_name']
        status_message_id = job_data.get('status_message_id')
        
        try:
            # Update status: downloading
            self.update_job_status(job_id, 'processing', 'downloading')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "⏳ Обработка: загружаю файл...")
            
            # Download file
            tg_file_path = self.telegram.get_file_path(file_id)
            if not tg_file_path:
                raise Exception("Failed to get file path")
                
            local_audio_path = self.telegram.download_file(tg_file_path)
            if not local_audio_path:
                raise Exception("Failed to download file")
                
            # Update status: converting
            self.update_job_status(job_id, 'processing', 'converting')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "⏳ Обработка: конвертирую аудио...")
            
            # Convert to MP3 with moderate bitrate for reliability
            converted_mp3_path = None
            if self.audio_service:
                converted_mp3_path = self.audio_service.convert_to_mp3(local_audio_path)
                # Clean up source file immediately
                if os.path.exists(local_audio_path):
                    os.remove(local_audio_path)
                    gc.collect()  # Force garbage collection
            else:
                # Fallback to legacy FFmpeg implementation
                try:
                    converted_mp3_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name
                    # Use memory-efficient FFmpeg settings
                    ffmpeg_command = [
                        'ffmpeg', '-y',
                        '-i', local_audio_path,
                        '-b:a', '128k',
                        '-ar', '44100',
                        '-ac', '1',  # Mono to save memory
                        '-threads', '1',  # Single thread to reduce memory
                        converted_mp3_path
                    ]
                    
                    process = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True, timeout=60)
                finally:
                    # Clean up source file immediately
                    if os.path.exists(local_audio_path):
                        os.remove(local_audio_path)
                        gc.collect()  # Force garbage collection
            
            # Update status: transcribing
            self.update_job_status(job_id, 'processing', 'transcribing')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "⏳ Обработка: распознаю речь...")
            
            # Transcribe
            transcribed_text = None
            try:
                transcribed_text = self.transcribe_audio(converted_mp3_path)
            finally:
                # Clean up converted file immediately
                if converted_mp3_path and os.path.exists(converted_mp3_path):
                    os.remove(converted_mp3_path)
                    gc.collect()  # Force garbage collection
            
            if not transcribed_text:
                raise Exception("Failed to transcribe audio")
                
            # Update status: formatting
            self.update_job_status(job_id, 'processing', 'formatting')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "⏳ Обработка: форматирую текст...")
            
            # Format text
            formatted_text = self.format_text_with_gemini(transcribed_text)
            
            # Log success
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'success', len(formatted_text))
            
            # Save result
            result = {
                'transcribed_text': transcribed_text,
                'formatted_text': formatted_text,
                'char_count': len(formatted_text)
            }
            self.update_job_status(job_id, 'completed', result=result)
            
            # Send result
            self._send_result_to_user(user_id, chat_id, formatted_text, status_message_id)
            
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg error: {e}")
            self.update_job_status(job_id, 'failed', error='audio_conversion_failed')
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_codec')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "❌ Ошибка: не удалось обработать аудиокодек файла. Попробуйте другой формат.")
                    
        except Exception as e:
            logging.error(f"Error processing job {job_id}: {e}")
            self.update_job_status(job_id, 'failed', error=str(e))
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_general')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "❌ Произошла ошибка при обработке файла. Попробуйте позже.")
                    
    def _log_transcription_attempt(self, user_id: int, user_name: str, file_size: int, 
                                  duration: int, status: str, char_count: int = 0):
        """Log transcription attempt to Firestore"""
        try:
            log_data = {
                'user_id': str(user_id),
                'editor_name': user_name,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'file_size': file_size,
                'duration': duration,
                'status': status,
                'char_count': char_count
            }
            if self.firestore_service:
                self.firestore_service.log_transcription(log_data)
            else:
                log_ref = self.db.collection('transcription_logs').document()
                log_ref.set(log_data)
        except Exception as e:
            logging.error(f"Error logging attempt: {e}")
            
    def _send_result_to_user(self, user_id: int, chat_id: int, formatted_text: str, status_message_id: int = None):
        """Send transcription result to user"""
        MAX_MESSAGE_LENGTH = 4000
        
        # Get user settings
        settings = self.firestore_service.get_user_settings(user_id) if self.firestore_service else {'use_code_tags': False}
        use_code_tags = settings.get('use_code_tags', False)
        
        # Get first sentence for caption
        import re
        match = re.search(r'^.*?[.!?](?=\s|$)', formatted_text, re.DOTALL)
        caption = match.group(0) if match else formatted_text.split('\n')[0]
        if len(caption) > 1024:
            caption = caption[:1021] + "..."
            
        # If text is too long, send as file
        if len(formatted_text) > MAX_MESSAGE_LENGTH:
            from datetime import datetime
            import pytz
            
            moscow_tz = pytz.timezone("Europe/Moscow")
            now_moscow = datetime.now(moscow_tz)
            file_name = now_moscow.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
            temp_txt_path = os.path.join('/tmp', file_name)
            
            with open(temp_txt_path, 'w', encoding='utf-8') as f:
                f.write(formatted_text)
                
            self.telegram.send_document(chat_id, temp_txt_path, caption=caption)
            os.remove(temp_txt_path)
            
            # Delete status message
            if status_message_id:
                self.telegram.delete_message(chat_id, status_message_id)
        else:
            # Send as message based on user preference
            if use_code_tags:
                escaped_text = formatted_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                if status_message_id:
                    self.telegram.edit_message_text(chat_id, status_message_id, 
                        f"<code>{escaped_text}</code>", parse_mode="HTML")
                else:
                    self.telegram.send_message(chat_id, f"<code>{escaped_text}</code>", parse_mode="HTML")
            else:
                # Send as plain text
                if status_message_id:
                    self.telegram.edit_message_text(chat_id, status_message_id, formatted_text)
                else:
                    self.telegram.send_message(chat_id, formatted_text)


def handle_pubsub_message(event, context):
    """Cloud Function entry point for Pub/Sub messages"""
    try:
        # Initialize services (only happens once per instance)
        telegram, openai, db, firestore_service, audio_service = initialize_services()
        
        # Create processor with shared services
        processor = AudioProcessor(telegram, openai, db, firestore_service, audio_service)
        
        # Decode the Pub/Sub message
        pubsub_message = base64.b64decode(event['data']).decode('utf-8')
        job_data = json.loads(pubsub_message)
        
        # Log memory usage at start
        if HAS_PSUTIL:
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            logging.info(f"Memory usage at start: {memory_mb:.1f} MB")
        
        logging.info(f"Processing audio job: {job_data['job_id']}")
        processor.process_audio_job(job_data)
        
        # Log memory usage at end
        if HAS_PSUTIL:
            memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
            logging.info(f"Memory usage at end: {memory_mb:.1f} MB")
        
        # Clean up after processing
        del processor
        del job_data
        gc.collect()
        
        return 'OK'
    except Exception as e:
        logging.error(f"Error in handle_pubsub_message: {e}")
        # Raise to trigger retry
        raise