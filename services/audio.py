"""
Audio Service - Centralized audio processing operations for the Telegram Whisper Bot
"""
import os
import logging
import tempfile
import subprocess
import time
from typing import Optional, Tuple
from openai import OpenAI
import google.genai as genai


class AudioService:
    """Service for all audio processing operations"""
    
    # Audio processing constants
    AUDIO_BITRATE = '128k'
    AUDIO_SAMPLE_RATE = '44100'
    AUDIO_CHANNELS = '1'  # Mono for memory efficiency
    FFMPEG_THREADS = '1'  # Single thread for memory efficiency
    FFMPEG_TIMEOUT = 60  # seconds
    
    # File size limits
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    MAX_DURATION_SECONDS = 3600  # 1 hour
    
    def __init__(self, openai_api_key: str, metrics_service=None):
        """Initialize with OpenAI client"""
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.metrics_service = metrics_service
        
    def validate_audio_file(self, file_size: int, duration: int) -> Tuple[bool, Optional[str]]:
        """
        Validate audio file parameters
        Returns: (is_valid, error_message)
        """
        if file_size > self.MAX_FILE_SIZE:
            return False, f"Файл слишком большой ({file_size / 1024 / 1024:.1f} МБ). Максимальный размер: 20 МБ"
            
        if duration > self.MAX_DURATION_SECONDS:
            return False, f"Аудио слишком длинное ({duration / 60:.1f} минут). Максимальная длительность: 60 минут"
            
        return True, None
    
    def analyze_audio_quality(self, audio_path: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Analyze audio/video file quality and format before processing
        Returns: (is_processable, warning_message, audio_info)
        """
        audio_info = self.get_audio_info(audio_path)
        
        if not audio_info:
            return False, "Не удалось определить формат файла", None
            
        format_name = audio_info.get('format', 'unknown')
        codec = audio_info.get('codec', 'unknown')
        sample_rate = audio_info.get('sample_rate', 0)
        bit_rate = audio_info.get('bit_rate', 0)
        
        # Check if it's a video file
        if self.is_video_file(audio_path):
            # For video files, we need to check if there's an audio stream
            if sample_rate == 0 and bit_rate == 0:
                return False, "❌ Видео не содержит аудиодорожки", audio_info
            # Video formats are supported as long as they have audio
            return True, None, audio_info
        
        # Check for unsupported audio formats
        unsupported_formats = ['amr', 'speex', 'gsm']
        if any(fmt in format_name.lower() for fmt in unsupported_formats):
            return False, f"Формат {format_name} не поддерживается. Используйте MP3, WAV, M4A, OGG или отправьте видео.", audio_info
            
        # Check for very low quality
        warning = None
        if sample_rate > 0 and sample_rate < 16000:
            warning = f"⚠️ Низкая частота дискретизации ({sample_rate} Гц) может снизить качество распознавания"
        elif bit_rate > 0 and bit_rate < 64000:
            warning = f"⚠️ Низкий битрейт ({bit_rate/1000:.0f} кбит/с) может снизить качество распознавания"
            
        return True, warning, audio_info
        
    def convert_to_mp3(self, input_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Convert audio file to MP3 with standard settings
        Returns path to converted file or None on error
        """
        if not output_path:
            output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name
            
        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-b:a', self.AUDIO_BITRATE,
            '-ar', self.AUDIO_SAMPLE_RATE,
            '-ac', self.AUDIO_CHANNELS,
            '-threads', self.FFMPEG_THREADS,
            output_path
        ]
        
        try:
            logging.info(f"Converting audio: {input_path} -> {output_path}")
            process = subprocess.run(
                ffmpeg_command, 
                check=True, 
                capture_output=True, 
                text=True, 
                timeout=self.FFMPEG_TIMEOUT
            )
            logging.info(f"FFmpeg conversion successful. Output: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            logging.error(f"FFmpeg conversion timed out after {self.FFMPEG_TIMEOUT} seconds")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
            
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg conversion failed. Error: {e.stderr}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
            
    def extract_audio_from_video(self, video_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Extract audio track from video file
        Returns path to extracted audio file or None on error
        """
        if not output_path:
            output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name
            
        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-vn',  # No video output
            '-acodec', 'mp3',  # Audio codec
            '-b:a', self.AUDIO_BITRATE,
            '-ar', self.AUDIO_SAMPLE_RATE,
            '-ac', self.AUDIO_CHANNELS,
            '-threads', self.FFMPEG_THREADS,
            output_path
        ]
        
        try:
            logging.info(f"Extracting audio from video: {video_path} -> {output_path}")
            process = subprocess.run(
                ffmpeg_command, 
                check=True, 
                capture_output=True, 
                text=True, 
                timeout=self.FFMPEG_TIMEOUT
            )
            logging.info(f"Audio extraction successful. Output: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            logging.error(f"Audio extraction timed out after {self.FFMPEG_TIMEOUT} seconds")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
            
        except subprocess.CalledProcessError as e:
            logging.error(f"Audio extraction failed. Error: {e.stderr}")
            # Check if the error is due to no audio stream
            if "Stream map '0:a' matches no streams" in e.stderr or "does not contain any stream" in e.stderr:
                logging.error("Video file has no audio stream")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
            
    def transcribe_audio(self, audio_path: str, language: str = "ru") -> Optional[str]:
        """
        Transcribe audio using OpenAI Whisper
        Returns transcribed text or None on error
        """
        api_start_time = time.time()
        try:
            with open(audio_path, "rb") as audio_file:
                # Create file tuple with basename for proper MIME type detection
                file_tuple = (os.path.basename(audio_path), audio_file)
                
                transcription = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_tuple,
                    language=language,
                    response_format="json"
                )
            
            # Log API call metrics
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('whisper', api_duration, True)
                
            logging.info("Transcription successful")
            return transcription.text
            
        except Exception as e:
            # Log failed API call
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('whisper', api_duration, False, str(e))
                
            logging.error(f"Error during transcription: {e}")
            return None
            
    def format_text_with_gemini(self, text: str, model_name: str = "gemini-2.5-flash") -> str:
        """
        Format transcribed text using Google Gemini
        Returns formatted text or original text on error
        """
        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for formatting ({word_count} words), returning original")
            return text
            
        api_start_time = time.time()
        try:
            # Initialize the client with Vertex AI configuration
            client = genai.Client(
                vertexai=True,
                project=os.environ.get('GCP_PROJECT', 'editorials-robot'),
                location='europe-west1'
            )
            
            prompt = f"""
            ВАЖНО: Ты работаешь только как форматировщик текста. НИКОГДА не веди диалог с пользователем, не объясняй что тебе нужно, не проси дополнительную информацию.
            
            Твоя задача — отформатировать следующий транскрипт устной речи, улучшив его читаемость, но полностью сохранив исходный смысл, стиль и лексику автора.
            
            Правила:
            1.  **Формирование абзацев:** Объединяй несколько (обычно от 2 до 5) связанных по теме предложений в один абзац. Начинай новый абзац только при явной смене микро-темы или при переходе к новому аргументу в рассуждении. Избегай создания слишком коротких абзацев из одного предложения.
            2.  **Обработка предложений:** Сохраняй оригинальную структуру предложений. Вмешивайся и разбивай предложение на несколько частей только в тех случаях, когда оно становится **аномально длинным и громоздким** для чтения из-за обилия придаточных частей или перечислений.
            3.  **Строгое сохранение контента:** Категорически запрещено изменять слова, добавлять что-либо от себя или делать выводы. Твоя работа — это работа редактора-форматировщика, а не копирайтера. Сохрани исходный текст в максимальной близости к оригиналу, изменив только разбивку на абзацы и, в редких случаях, структуру самых длинных предложений.
            4.  **Если текст кажется неполным или обрывочным:** Просто верни его без изменений. НЕ объясняй, что текст неполный. НЕ проси больше информации. НЕ пиши инструкции.
            
            Исходный текст для обработки:
            ---
            {text}
            ---
            """
            
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            formatted_text = response.text
            
            # Log API call metrics
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, True)
            
            logging.info("Successfully formatted text with Gemini")
            return formatted_text
            
        except Exception as e:
            # Log failed API call
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, False, str(e))
                
            logging.error(f"Error calling Gemini API: {e}")
            return text
            
    def is_video_file(self, file_path: str) -> bool:
        """
        Check if the file is a video based on format detection
        """
        file_info = self.get_audio_info(file_path)
        if not file_info:
            # Fallback to extension check
            video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v', '.mpg', '.mpeg']
            return any(file_path.lower().endswith(ext) for ext in video_extensions)
            
        format_name = file_info.get('format', '').lower()
        video_formats = ['mp4', 'mov', 'avi', 'mkv', 'webm', 'matroska', 'mpeg', 'mpg']
        return any(fmt in format_name for fmt in video_formats)
        
    def process_audio_pipeline(self, audio_path: str, cleanup_source: bool = True) -> Tuple[Optional[str], Optional[str]]:
        """
        Full audio processing pipeline: convert -> transcribe -> format
        Returns: (transcribed_text, formatted_text) or (None, None) on error
        """
        converted_path = None
        extracted_audio_path = None
        
        try:
            # Check if it's a video file
            if self.is_video_file(audio_path):
                logging.info("Detected video file, extracting audio...")
                extracted_audio_path = self.extract_audio_from_video(audio_path)
                if not extracted_audio_path:
                    logging.error("Failed to extract audio from video")
                    return None, None
                # Use extracted audio for further processing
                processing_path = extracted_audio_path
            else:
                # Regular audio file
                processing_path = audio_path
            
            # Convert to MP3 (or ensure proper format)
            converted_path = self.convert_to_mp3(processing_path)
            if not converted_path:
                return None, None
                
            # Clean up source if requested
            if cleanup_source and os.path.exists(audio_path):
                os.remove(audio_path)
                
            # Clean up extracted audio if it was created
            if extracted_audio_path and os.path.exists(extracted_audio_path) and extracted_audio_path != converted_path:
                os.remove(extracted_audio_path)
                
            # Transcribe
            transcribed_text = self.transcribe_audio(converted_path)
            if not transcribed_text:
                return None, None
                
            # Check if Whisper returned the "continuation follows" phrase
            if transcribed_text.strip() == "Продолжение следует...":
                logging.warning("Whisper returned 'Продолжение следует...', indicating no speech detected")
                return None, "На записи не обнаружено речи или текст не был распознан."
                
            # Format
            formatted_text = self.format_text_with_gemini(transcribed_text)
            
            return transcribed_text, formatted_text
            
        finally:
            # Always clean up converted file
            if converted_path and os.path.exists(converted_path):
                os.remove(converted_path)
                
    def get_audio_info(self, audio_path: str) -> Optional[dict]:
        """
        Get audio file information using ffprobe
        Returns dict with duration, bitrate, format, etc.
        """
        try:
            ffprobe_command = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration,bit_rate,format_name',
                '-show_entries', 'stream=codec_name,sample_rate,channels',
                '-of', 'json',
                audio_path
            ]
            
            result = subprocess.run(
                ffprobe_command,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                return {
                    'duration': float(data.get('format', {}).get('duration', 0)),
                    'bit_rate': int(data.get('format', {}).get('bit_rate', 0)),
                    'format': data.get('format', {}).get('format_name', 'unknown'),
                    'codec': data.get('streams', [{}])[0].get('codec_name', 'unknown'),
                    'sample_rate': int(data.get('streams', [{}])[0].get('sample_rate', 0)),
                    'channels': int(data.get('streams', [{}])[0].get('channels', 0))
                }
                
        except Exception as e:
            logging.error(f"Error getting audio info: {e}")
            
        return None