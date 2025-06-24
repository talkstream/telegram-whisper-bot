import os
import json
import base64
import logging
import tempfile
import subprocess
import gc
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

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
        return _telegram_service, _openai_client, _db_client, _firestore_service, _audio_service
    
    logging.info("Initializing services for this instance...")
    
    # Import heavy libraries only when needed
    from google.cloud import firestore as fs
    from google.cloud import secretmanager
    from openai import OpenAI
    import vertexai
    from services.telegram import TelegramService
    from services.firestore import FirestoreService
    from services.audio import AudioService
    
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
    _audio_service = AudioService(openai_api_key)
    
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
        self.start_time = None
        self.total_stages = 5  # downloading, converting, transcribing, formatting, sending
        
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
    
    def calculate_progress(self, stage: int, sub_progress: float = 0) -> float:
        """Calculate overall progress percentage
        
        Args:
            stage: Current stage number (1-5)
            sub_progress: Progress within current stage (0-1)
            
        Returns:
            Overall progress percentage (0-100)
        """
        base_progress = (stage - 1) / self.total_stages * 100
        stage_progress = sub_progress / self.total_stages * 100
        return base_progress + stage_progress
    
    def estimate_total_time(self, duration_seconds: int) -> str:
        """Estimate total processing time based on audio duration
        
        More accurate model: processing_time = 12 + duration * 0.35
        (12 seconds base overhead + 35% of audio duration)
        
        Args:
            duration_seconds: Audio file duration in seconds
            
        Returns:
            Formatted time estimate string
        """
        # Updated formula based on 3-second pause reduction
        # Base overhead: 12 seconds (download, convert, send)
        # Processing rate: ~35% of audio duration
        total_estimate = 12 + (duration_seconds * 0.35)
        
        if total_estimate < 60:
            return f"~{int(total_estimate)} секунд"
        else:
            minutes = int(total_estimate / 60)
            seconds = int(total_estimate % 60)
            if seconds > 0:
                return f"~{minutes} мин. {seconds} сек."
            else:
                return f"~{minutes} мин."
        
        
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
        
        # Start timing
        self.start_time = time.time()
        
        try:
            # Stage 1: Downloading
            stage = 1
            self.update_job_status(job_id, 'processing', 'downloading')
            if status_message_id:
                progress = self.calculate_progress(stage, 0)
                time_estimate = self.estimate_total_time(duration)
                # Show time estimate immediately
                self.telegram.edit_message_text(
                    chat_id, status_message_id, 
                    f"⏳ Загружаю файл...\nОжидаемое время: {time_estimate}"
                )
                # Give user 3 seconds to read the estimate (non-blocking would require major refactoring)
                time.sleep(3)
            
            # Download file
            tg_file_path = self.telegram.get_file_path(file_id)
            if not tg_file_path:
                raise Exception("Failed to get file path")
                
            local_audio_path = self.telegram.download_file(tg_file_path)
            if not local_audio_path:
                raise Exception("Failed to download file")
            
            # Check audio quality before processing
            if self.audio_service:
                is_processable, quality_msg, audio_info = self.audio_service.analyze_audio_quality(local_audio_path)
                if not is_processable:
                    raise Exception(f"Audio quality check failed: {quality_msg}")
                # Don't log quality warnings unless processing fails
                pass
                
            # Stage 2: Converting
            stage = 2
            self.update_job_status(job_id, 'processing', 'converting')
            if status_message_id:
                progress = self.calculate_progress(stage, 0)
                self.telegram.send_progress_update(
                    chat_id, status_message_id, 
                    "Конвертирую аудио...", progress
                )
            
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
            
            # Get actual duration and audio info from FFmpeg before transcribing
            actual_duration = None
            audio_format = None
            audio_codec = None
            audio_sample_rate = None
            audio_bitrate = None
            if self.audio_service and converted_mp3_path:
                audio_info = self.audio_service.get_audio_info(converted_mp3_path)
                if audio_info:
                    actual_duration = audio_info.get('duration', 0)
                    audio_format = audio_info.get('format', 'unknown')
                    audio_codec = audio_info.get('codec', 'unknown')
                    audio_sample_rate = audio_info.get('sample_rate', 0)
                    audio_bitrate = audio_info.get('bit_rate', 0)
                    logging.info(f"FFmpeg reported duration: {actual_duration}s vs Telegram duration: {duration}s")
                    logging.info(f"Audio info: format={audio_format}, codec={audio_codec}, sample_rate={audio_sample_rate}, bitrate={audio_bitrate}")
            
            # Stage 3: Transcribing
            stage = 3
            self.update_job_status(job_id, 'processing', 'transcribing')
            if status_message_id:
                progress = self.calculate_progress(stage, 0)
                self.telegram.send_progress_update(
                    chat_id, status_message_id, 
                    "Распознаю речь...", progress
                )
            
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
                
            # Stage 4: Formatting
            stage = 4
            self.update_job_status(job_id, 'processing', 'formatting')
            if status_message_id:
                progress = self.calculate_progress(stage, 0)
                self.telegram.send_progress_update(
                    chat_id, status_message_id, 
                    "Форматирую текст...", progress
                )
            
            # Format text
            formatted_text = self.format_text_with_gemini(transcribed_text)
            
            # Update progress after formatting
            if status_message_id:
                progress = self.calculate_progress(stage, 0.8)
                self.telegram.send_progress_update(
                    chat_id, status_message_id, 
                    "Подготавливаю результат...", progress
                )
            
            # Skip intermediate updates - go directly to sending result
            # because _send_result_to_user will edit or delete the message anyway
            
            # Calculate processing time
            processing_time = int(time.time() - self.start_time) if self.start_time else None
            
            # Log success with both durations and additional metadata
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'success', len(formatted_text), 
                                          telegram_duration=duration, ffmpeg_duration=actual_duration,
                                          audio_format=audio_format, audio_codec=audio_codec,
                                          audio_sample_rate=audio_sample_rate, audio_bitrate=audio_bitrate,
                                          processing_time=processing_time)
            
            # Send result first (this will edit/delete the status message)
            self._send_result_to_user(user_id, chat_id, formatted_text, status_message_id)
            
            # Save result after sending
            result = {
                'transcribed_text': transcribed_text,
                'formatted_text': formatted_text,
                'char_count': len(formatted_text),
                'telegram_duration': duration,
                'ffmpeg_duration': actual_duration
            }
            self.update_job_status(job_id, 'completed', result=result)
            
            # Deduct minutes after successful processing (for all users, including owner)
            # Use FFmpeg duration if available as it's more accurate
            billing_duration = int(actual_duration) if actual_duration else duration
            duration_minutes = max(1, (billing_duration + 59) // 60)  # Round up to nearest minute
            if self.firestore_service:
                # Update user balance
                user_ref = self.db.collection('users').document(str(user_id))
                user_ref.update({
                    'balance_minutes': firestore.Increment(-duration_minutes),
                    'last_seen': firestore.SERVER_TIMESTAMP
                })
                logging.info(f"Deducted {duration_minutes} minutes from user {user_id} balance")
            
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg error: {e}")
            self.update_job_status(job_id, 'failed', error='audio_conversion_failed')
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_codec')
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id, 
                    "❌ Ошибка обработки аудио\n\n"
                    "Не удалось распознать формат файла.\n\n"
                    "💡 Рекомендации:\n"
                    "• Попробуйте конвертировать в MP3/WAV\n"
                    "• Используйте стандартные аудиокодеки\n"
                    "• Проверьте, что файл не поврежден")
                    
        except Exception as e:
            logging.error(f"Error processing job {job_id}: {e}")
            self.update_job_status(job_id, 'failed', error=str(e))
            self._log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_general')
            if status_message_id:
                error_msg = "❌ Ошибка обработки\n\n"
                
                if "Failed to transcribe" in str(e):
                    error_msg += "Не удалось распознать речь.\n\n"
                    error_msg += "💡 Рекомендации:\n"
                    error_msg += "• Убедитесь, что файл содержит речь\n"
                    error_msg += "• Проверьте качество записи\n"
                    error_msg += "• Попробуйте улучшить качество аудио"
                elif "Failed to download" in str(e):
                    error_msg += "Не удалось загрузить файл.\n\n"
                    error_msg += "💡 Попробуйте отправить файл заново."
                else:
                    error_msg += "Произошла неожиданная ошибка.\n\n"
                    error_msg += "💡 Попробуйте позже или обратитесь в поддержку."
                    
                self.telegram.edit_message_text(chat_id, status_message_id, error_msg)
                    
    def _log_transcription_attempt(self, user_id: int, user_name: str, file_size: int, 
                                  duration: int, status: str, char_count: int = 0,
                                  telegram_duration: int = None, ffmpeg_duration: float = None,
                                  audio_format: str = None, audio_codec: str = None,
                                  audio_sample_rate: int = None, audio_bitrate: int = None,
                                  processing_time: int = None):
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
            # Add new duration fields if available
            if telegram_duration is not None:
                log_data['telegram_duration'] = telegram_duration
            if ffmpeg_duration is not None:
                log_data['ffmpeg_duration'] = int(ffmpeg_duration)  # Store as int seconds
            # Add audio metadata if available
            if audio_format:
                log_data['audio_format'] = audio_format
            if audio_codec:
                log_data['audio_codec'] = audio_codec
            if audio_sample_rate:
                log_data['audio_sample_rate'] = audio_sample_rate
            if audio_bitrate:
                log_data['audio_bitrate'] = audio_bitrate
            if processing_time is not None:
                log_data['processing_time'] = processing_time
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