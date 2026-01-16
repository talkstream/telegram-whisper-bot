"""
Audio Service - Centralized audio processing operations for the Telegram Whisper Bot
"""
import os
import logging
import tempfile
import subprocess
import time
from typing import Optional, Tuple
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
    
    def __init__(self, metrics_service=None, openai_client=None):
        """Initialize AudioService"""
        self.metrics_service = metrics_service
        self.openai_client = openai_client
        
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
            
    def transcribe_with_ffmpeg_whisper(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio using FFmpeg 8.0 built-in Whisper filter.

        Args:
            audio_path: Path to audio file (any format supported by FFmpeg)
            language: Language code (default: 'ru' for Russian)

        Returns:
            Transcribed text as string

        Raises:
            subprocess.TimeoutExpired: If transcription takes too long
            subprocess.CalledProcessError: If FFmpeg fails
        """
        import subprocess
        import json
        import os
        import re

        # Get Whisper model path from environment
        model_path = os.getenv('WHISPER_MODEL_PATH', '/opt/whisper/models/ggml-base.bin')

        # Temporary output file for transcription
        output_json = f"{audio_path}.transcript.json"

        try:
            # FFmpeg command with Whisper filter
            # Parameters:
            #   model: path to ggml model file
            #   language: transcription language
            #   format: json (for structured output)
            #   use_gpu: true (auto-detect GPU, fallback to CPU)
            #   queue: 6 (balance between quality and processing frequency)
            ffmpeg_command = [
                'ffmpeg',
                '-i', audio_path,
                '-vn',  # No video
                '-af', (
                    f"whisper="
                    f"model={model_path}:"
                    f"language={language}:"
                    f"format=json:"
                    f"use_gpu=true:"
                    f"queue=6"
                ),
                '-f', 'null',
                '-'
            ]

            # Execute FFmpeg with timeout
            # Timeout: 3x audio duration (conservative estimate)
            audio_duration = self.get_audio_duration(audio_path)
            timeout = max(int(audio_duration * 3), 60)  # Minimum 60s

            logging.info(f"Starting FFmpeg Whisper transcription (timeout: {timeout}s)")

            result = subprocess.run(
                ffmpeg_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True
            )

            # Parse output from stderr (FFmpeg logs to stderr)
            transcription_text = self._parse_ffmpeg_whisper_output(result.stderr)

            if not transcription_text or len(transcription_text.strip()) < 5:
                # Log stderr for debugging if empty
                logging.warning(f"Whisper returned empty. Stderr: {result.stderr[:500]}")
                raise ValueError("Whisper returned empty or invalid transcription")

            logging.info(f"FFmpeg Whisper transcription completed: {len(transcription_text)} chars")
            return transcription_text

        except subprocess.TimeoutExpired:
            logging.error(f"FFmpeg Whisper transcription timeout after {timeout}s")
            raise
        except subprocess.CalledProcessError as e:
            logging.error(f"FFmpeg Whisper failed: {e.stderr}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error in FFmpeg Whisper: {str(e)}")
            raise
        finally:
            # Cleanup temporary files if any (though we output to stdout/stderr mostly)
            if os.path.exists(output_json):
                os.remove(output_json)


    def _parse_ffmpeg_whisper_output(self, ffmpeg_stderr: str) -> str:
        """
        Parse transcription text from FFmpeg stderr output.

        FFmpeg Whisper filter outputs transcription segments to stderr.
        Format: [whisper @ 0x...] segment_text

        Args:
            ffmpeg_stderr: FFmpeg stderr output

        Returns:
            Concatenated transcription text
        """
        import re
        import json

        # 1. Try to parse JSON output if present (since we used format=json)
        # JSON output usually comes in blocks or as a single JSON object depending on version
        try:
            # Extract JSON-like blocks. This is tricky because stderr mixes logs.
            # Look for lines starting with { and ending with }
            # Or scan for "text": "..." patterns if valid JSON isn't easily extractable
            
            # Simple approach: Extract "text" fields if standard JSON structure is found
            # Note: FFmpeg Whisper JSON format might vary.
            # Let's try to find valid JSON blocks first.
            json_pattern = r'\{.*?"text":\s*".*?".*?\}' 
            json_matches = re.findall(json_pattern, ffmpeg_stderr, re.DOTALL)
            
            texts = []
            for json_str in json_matches:
                try:
                    data = json.loads(json_str)
                    if 'text' in data:
                        texts.append(data['text'])
                except:
                    continue
            
            if texts:
                return ' '.join(texts).strip()

        except Exception as e:
            pass

        # 2. Fallback: Parse standard log format
        # Pattern: [whisper @ 0xaddress] transcribed_text
        pattern = r'\[whisper @ 0x[0-9a-f]+\]\s+(.+)'
        matches = re.findall(pattern, ffmpeg_stderr)

        if matches:
             return ' '.join(matches).strip()

        # 3. Last resort: Return cleaned stderr
        # logging.warning("Could not parse structured Whisper output, using raw stderr cleanup")
        cleaned = re.sub(r'\[.*?\].*?(?:\n|$)', '', ffmpeg_stderr)
        return cleaned.strip()


    def transcribe_audio(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio file using FFmpeg 8.0 Whisper (primary method).

        This method uses FFmpeg's built-in Whisper filter for local transcription.
        NO external API calls, NO OpenAI costs.

        Args:
            audio_path: Path to audio file
            language: Language code (default: 'ru' for Russian)

        Returns:
            Transcribed text
        """
        try:
            # Use FFmpeg 8.0 Whisper (local, fast, free)
            transcription = self.transcribe_with_ffmpeg_whisper(audio_path, language=language)

            # Quality check
            if not transcription or len(transcription) < 5:
                raise ValueError("Transcription too short or empty")

            # Check for common Whisper errors
            if transcription.strip().lower() in ['продолжение следует...', '[blank_audio]', '...']:
                raise ValueError("No speech detected in audio")

            return transcription

        except Exception as e:
            logging.error(f"FFmpeg Whisper transcription failed: {str(e)}")
            raise

    def get_audio_duration(self, audio_path: str) -> float:
        """
        Get audio duration in seconds using ffprobe.

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds
        """
        import subprocess
        import json

        try:
            result = subprocess.run(
                [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    audio_path
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10
            )

            data = json.loads(result.stdout)
            duration = float(data['format']['duration'])
            return duration
        except Exception as e:
            logging.warning(f"Could not get audio duration: {e}, using default 600s")
            return 600.0  # Default 10 minutes
            
    def format_text_with_gemini(self, text: str, use_code_tags: bool = False, use_yo: bool = True) -> str:
        """
        Format transcribed text using Gemini 3 Flash.

        Gemini 3 Flash optimizations:
        - More concise prompts work better
        - Explicit instructions about NOT adding commentary
        - Temperature 0.3 for consistency
        """
        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for formatting ({word_count} words), returning original")
            return text

        api_start_time = time.time()
        try:
            # Initialize the client with Vertex AI configuration
            # Switch to us-central1 for Gemini 3 availability
            client = genai.Client(
                vertexai=True,
                project=os.environ.get('GCP_PROJECT', 'editorials-robot'),
                location='us-central1'
            )
            
            # Prepare user settings for prompt
            code_tag_instruction = (
                "Оберни ВЕСЬ текст в теги <code></code>."
                if use_code_tags else
                "НЕ используй теги <code>."
            )

            yo_instruction = (
                "Сохраняй букву ё где она есть."
                if use_yo else
                "Заменяй все буквы ё на е."
            )

            # Optimized prompt for Gemini 3
            prompt = f"""Отформатируй транскрипцию аудиозаписи. Правила:

1. Исправь ошибки распознавания речи
2. Добавь знаки препинания
3. Раздели на абзацы по смыслу
4. {code_tag_instruction}
5. {yo_instruction}
6. ВАЖНО: НЕ добавляй свои комментарии, НЕ веди диалог с пользователем
7. Если текст короче 10 слов - верни как есть

Текст для форматирования:

{text}"""

            # Generate with Gemini 3 Flash (Preview)
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
                config={
                    'temperature': 0.3,  # Low temperature for consistency
                    'top_p': 0.95,
                    'max_output_tokens': 8192,
                    # 'thinking_level': 1,  # Control reasoning depth (0-3) - Uncomment if supported
                }
            )

            formatted_text = response.text.strip()

            # Remove code tags if present but not wanted
            if not use_code_tags and formatted_text.startswith('<code>'):
                formatted_text = formatted_text.replace('<code>', '').replace('</code>', '')

            # Quality check
            if len(formatted_text) < 5:
                logging.warning("Gemini returned very short text, using original")
                return text
            
            # Log API call metrics
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, True)
            
            logging.info("Successfully formatted text with Gemini 3")
            return formatted_text

        except Exception as e:
            # Log failed API call
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, False, str(e))

            logging.error(f"Gemini 3 formatting failed: {str(e)}")
            # Fallback: return original text
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