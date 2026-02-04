"""
Audio Service - Centralized audio processing operations for the Telegram Whisper Bot

v3.0.0: Alibaba Cloud migration - Qwen LLM for formatting with Gemini fallback
v2.1.0: Added Alibaba ASR backend option (requires OSS for local files)
v2.0.0: Added faster-whisper GPU support as alternative backend
"""
import os
import logging
import tempfile
import subprocess
import time
from typing import Optional, Tuple


class AudioService:
    """Service for all audio processing operations"""

    # Audio processing constants
    AUDIO_BITRATE = '32k'  # Optimized: was 64k, 32k is sufficient for ASR (v3.0.1)
    AUDIO_SAMPLE_RATE = '16000'  # 16kHz for ASR compatibility
    AUDIO_CHANNELS = '1'  # Mono for memory efficiency
    FFMPEG_THREADS = '4'  # Optimized for speed (was 1)
    FFMPEG_TIMEOUT = 60  # seconds

    # File size limits
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    MAX_DURATION_SECONDS = 3600  # 1 hour

    # Whisper backend options
    BACKEND_OPENAI = 'openai'
    BACKEND_FASTER_WHISPER = 'faster-whisper'
    BACKEND_QWEN_ASR = 'qwen-asr'  # Alibaba Qwen3-ASR (fastest: 92ms TTFT)

    def __init__(self, metrics_service=None, openai_client=None, whisper_backend: str = None,
                 alibaba_api_key: str = None, oss_config: dict = None):
        """
        Initialize AudioService

        Args:
            metrics_service: Optional metrics tracking service
            openai_client: OpenAI client instance (for openai backend)
            whisper_backend: Whisper backend to use ('openai', 'faster-whisper', 'qwen-asr')
                            If None, auto-detected from environment or defaults to 'openai'
            alibaba_api_key: Alibaba DashScope API key (for qwen-asr backend)
            oss_config: Alibaba OSS configuration dict with keys:
                        - bucket: OSS bucket name
                        - endpoint: OSS endpoint (e.g., oss-eu-central-1.aliyuncs.com)
                        - access_key_id: Alibaba AccessKey ID
                        - access_key_secret: Alibaba AccessKey Secret
        """
        self.metrics_service = metrics_service
        self.openai_client = openai_client

        # Determine whisper backend
        self.whisper_backend = whisper_backend or os.environ.get('WHISPER_BACKEND', self.BACKEND_OPENAI)

        # Alibaba API key for Qwen3-ASR
        self.alibaba_api_key = alibaba_api_key or os.environ.get('ALIBABA_API_KEY')

        # Alibaba OSS configuration
        self.oss_config = oss_config or {}
        self._oss_bucket = None  # Lazy-loaded OSS bucket

        # Faster-whisper model (lazy-loaded)
        self._faster_whisper_model = None
        self._faster_whisper_model_name = os.environ.get(
            'WHISPER_MODEL',
            'dvislobokov/faster-whisper-large-v3-turbo-russian'
        )
        
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
        Convert audio file to MP3 with smart settings based on duration.
        Uses minimal settings for short audio (<10 sec) for faster processing.
        Returns path to converted file or None on error
        """
        if not output_path:
            output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name

        # Smart settings based on audio duration (v3.0.1)
        duration = self.get_audio_duration(input_path)
        if duration < 10:
            # Ultra-light settings for short audio: 24kbps, 8kHz (still good enough for ASR)
            bitrate = '24k'
            sample_rate = '8000'
            logging.info(f"Short audio ({duration:.1f}s) - using light settings: {bitrate}, {sample_rate}Hz")
        else:
            # Standard settings
            bitrate = self.AUDIO_BITRATE
            sample_rate = self.AUDIO_SAMPLE_RATE

        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-b:a', bitrate,
            '-ar', sample_rate,
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
            
    def transcribe_audio(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio file using configured backend.

        Args:
            audio_path: Path to audio file
            language: Language code (default: 'ru' for Russian)

        Returns:
            Transcribed text

        Backends:
            - 'qwen-asr': Alibaba Qwen3-ASR (92ms TTFT, fastest)
            - 'openai': OpenAI Whisper API ($0.006/min)
            - 'faster-whisper': Local GPU inference ($0.24/hour on Spot T4)
        """
        logging.info(f"Transcription backend: {self.whisper_backend}")

        # Route to appropriate backend
        if self.whisper_backend == self.BACKEND_QWEN_ASR:
            return self.transcribe_with_qwen_asr(audio_path, language)
        elif self.whisper_backend == self.BACKEND_FASTER_WHISPER:
            return self.transcribe_with_faster_whisper(audio_path, language)
        elif self.whisper_backend == self.BACKEND_OPENAI and self.openai_client:
            return self.transcribe_with_openai(audio_path, language)
        elif self.openai_client:
            # Default to OpenAI if client is available
            logging.info("Starting OpenAI Whisper transcription...")
            return self.transcribe_with_openai(audio_path, language)

        # Fallback to local FFmpeg Whisper (legacy, only if nothing else available)
        logging.warning("No OpenAI client and faster-whisper not configured, falling back to FFmpeg Whisper...")
        try:
            transcription = self.transcribe_with_ffmpeg_whisper(audio_path, language=language)

            if not transcription or len(transcription) < 5:
                raise ValueError("Transcription too short or empty")

            if transcription.strip().lower() in ['продолжение следует...', '[blank_audio]', '...']:
                raise ValueError("No speech detected in audio")

            return transcription

        except Exception as e:
            logging.error(f"FFmpeg Whisper transcription failed: {str(e)}")
            raise

    def transcribe_with_faster_whisper(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio using faster-whisper with GPU acceleration.

        Cost: ~$0.24/hour on GCP Spot T4 (33% cheaper than OpenAI API)
        Model: dvislobokov/faster-whisper-large-v3-turbo-russian

        Args:
            audio_path: Path to audio file
            language: Language code

        Returns:
            Transcribed text
        """
        # Lazy load faster-whisper model
        if self._faster_whisper_model is None:
            self._initialize_faster_whisper()

        logging.info(f"Starting faster-whisper transcription: {audio_path}")
        start_time = time.time()

        try:
            segments, info = self._faster_whisper_model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=400
                )
            )

            # Collect all segments
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text.strip())

            full_text = " ".join(text_parts)

            duration = time.time() - start_time
            logging.info(f"Faster-whisper completed in {duration:.2f}s, {len(full_text)} chars")

            # Log API call metrics
            if self.metrics_service:
                self.metrics_service.log_api_call('faster-whisper', duration, True)

            # Quality check
            if not full_text or len(full_text.strip()) < 5:
                raise ValueError("Transcription too short or empty")

            return full_text

        except Exception as e:
            duration = time.time() - start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('faster-whisper', duration, False, str(e))
            logging.error(f"Faster-whisper error: {e}")
            raise

    def _initialize_faster_whisper(self):
        """Initialize faster-whisper model (lazy loading)"""
        try:
            from faster_whisper import WhisperModel
            import torch

            # Detect GPU availability
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"

            logging.info(f"Initializing faster-whisper: model={self._faster_whisper_model_name}, "
                        f"device={device}, compute_type={compute_type}")

            self._faster_whisper_model = WhisperModel(
                self._faster_whisper_model_name,
                device=device,
                compute_type=compute_type
            )

            logging.info("Faster-whisper model loaded successfully")

        except ImportError:
            raise RuntimeError(
                "faster-whisper not installed. Install with: pip install faster-whisper torch"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to initialize faster-whisper: {e}")

    def _get_oss_bucket(self):
        """Get or create OSS bucket connection (lazy loading)"""
        if self._oss_bucket is not None:
            return self._oss_bucket

        if not self.oss_config:
            return None

        try:
            import oss2

            bucket_name = self.oss_config.get('bucket')
            endpoint = self.oss_config.get('endpoint')
            access_key_id = self.oss_config.get('access_key_id')
            access_key_secret = self.oss_config.get('access_key_secret')

            if not all([bucket_name, endpoint, access_key_id, access_key_secret]):
                logging.warning("Incomplete OSS configuration")
                return None

            auth = oss2.Auth(access_key_id, access_key_secret)
            self._oss_bucket = oss2.Bucket(auth, endpoint, bucket_name)
            logging.info(f"OSS bucket initialized: {bucket_name}")
            return self._oss_bucket

        except ImportError:
            logging.warning("oss2 not installed")
            return None
        except Exception as e:
            logging.error(f"Failed to initialize OSS bucket: {e}")
            return None

    def _upload_to_oss(self, local_path: str) -> Optional[str]:
        """
        Upload file to Alibaba OSS and return the OSS URL.

        Args:
            local_path: Path to local file

        Returns:
            OSS URL (oss://bucket/key) or None on failure
        """
        bucket = self._get_oss_bucket()
        if not bucket:
            return None

        try:
            import os
            import uuid

            # Generate unique key
            file_ext = os.path.splitext(local_path)[1] or '.mp3'
            oss_key = f"audio/{uuid.uuid4().hex}{file_ext}"

            # Upload file
            logging.info(f"Uploading to OSS: {local_path} -> {oss_key}")
            bucket.put_object_from_file(oss_key, local_path)

            # Return OSS URL
            bucket_name = self.oss_config.get('bucket')
            oss_url = f"oss://{bucket_name}/{oss_key}"
            logging.info(f"Uploaded to OSS: {oss_url}")

            return oss_url

        except Exception as e:
            logging.error(f"OSS upload failed: {e}")
            return None

    def _delete_from_oss(self, oss_url: str):
        """Delete file from OSS after transcription"""
        bucket = self._get_oss_bucket()
        if not bucket or not oss_url:
            return

        try:
            # Extract key from oss://bucket/key
            parts = oss_url.replace('oss://', '').split('/', 1)
            if len(parts) == 2:
                oss_key = parts[1]
                bucket.delete_object(oss_key)
                logging.info(f"Deleted from OSS: {oss_key}")
        except Exception as e:
            logging.warning(f"Failed to delete from OSS: {e}")

    def transcribe_with_qwen_asr(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio using Qwen3-ASR-Flash via DashScope REST API.

        Uses the modern Qwen3-ASR model (2026) with file-based transcription.
        Model: qwen3-asr-flash (fast, multilingual, 52 languages)

        Uses pure requests - no dashscope SDK needed!

        Args:
            audio_path: Path to audio file (MP3, WAV, etc.)
            language: Language code (default: 'ru' for Russian)

        Returns:
            Transcribed text
        """
        import base64
        import pathlib
        import requests

        api_key = self.alibaba_api_key or os.environ.get('DASHSCOPE_API_KEY')
        if not api_key:
            logging.warning("DASHSCOPE_API_KEY not configured")
            raise RuntimeError("DASHSCOPE_API_KEY not configured")

        logging.info(f"Starting Qwen3-ASR-Flash transcription: {audio_path}")
        start_time = time.time()

        try:
            # Get file info
            file_path = pathlib.Path(audio_path)
            file_size = file_path.stat().st_size
            logging.info(f"Audio file size: {file_size} bytes")

            # Determine MIME type
            suffix = file_path.suffix.lower()
            mime_types = {
                '.mp3': 'audio/mpeg',
                '.wav': 'audio/wav',
                '.ogg': 'audio/ogg',
                '.m4a': 'audio/mp4',
                '.flac': 'audio/flac',
                '.webm': 'audio/webm'
            }
            audio_mime_type = mime_types.get(suffix, 'audio/mpeg')

            # Encode audio as base64 data URI
            base64_str = base64.b64encode(file_path.read_bytes()).decode('utf-8')
            data_uri = f"data:{audio_mime_type};base64,{base64_str}"

            logging.info(f"Encoded audio to base64 ({len(base64_str)} chars)")

            # DashScope MultiModalConversation API endpoint (international)
            url = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            # Request payload
            payload = {
                "model": "qwen3-asr-flash",
                "input": {
                    "messages": [
                        {"role": "system", "content": [{"text": ""}]},
                        {"role": "user", "content": [{"audio": data_uri}]}
                    ]
                },
                "parameters": {
                    "result_format": "message",
                    "asr_options": {
                        "enable_itn": True
                    }
                }
            }

            logging.info("Calling Qwen3-ASR-Flash API...")
            response = requests.post(url, headers=headers, json=payload, timeout=120)

            duration = time.time() - start_time
            logging.info(f"API response received in {duration:.2f}s, status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                logging.info(f"Response: {str(data)[:500]}...")

                # Extract transcription from response
                full_text = ""

                # Try different response structures
                output = data.get('output', {})

                # Structure 1: output.choices[0].message.content[0].text
                choices = output.get('choices', [])
                if choices:
                    message = choices[0].get('message', {})
                    content = message.get('content', [])
                    if isinstance(content, list) and content:
                        full_text = content[0].get('text', '')
                    elif isinstance(content, str):
                        full_text = content

                # Structure 2: output.text
                if not full_text:
                    full_text = output.get('text', '')

                # Structure 3: direct text in output
                if not full_text and isinstance(output, str):
                    full_text = output

                full_text = full_text.strip()
                logging.info(f"Qwen3-ASR completed in {duration:.2f}s, {len(full_text)} chars")

                if self.metrics_service:
                    self.metrics_service.log_api_call('qwen3-asr', duration, True)

                if not full_text or len(full_text) < 3:
                    logging.warning("Qwen3-ASR returned empty result")
                    raise ValueError("Transcription empty")

                return full_text

            else:
                # API error
                error_data = response.json() if response.text else {}
                error_code = error_data.get('code', response.status_code)
                error_msg = error_data.get('message', response.text[:200])
                logging.error(f"Qwen3-ASR API error: {error_code} - {error_msg}")
                raise RuntimeError(f"API error: {error_code} - {error_msg}")

        except requests.RequestException as e:
            duration = time.time() - start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('qwen3-asr', duration, False, str(e))
            logging.error(f"Qwen3-ASR request error: {e}")
            raise RuntimeError(f"Transcription failed: {e}")

        except Exception as e:
            duration = time.time() - start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('qwen3-asr', duration, False, str(e))
            logging.error(f"Qwen3-ASR error: {e}")
            raise RuntimeError(f"Transcription failed: {e}")

    def _convert_to_pcm(self, input_path: str) -> Optional[str]:
        """
        Convert audio file to PCM WAV format (16kHz, mono, 16-bit) for ASR.

        Args:
            input_path: Path to input audio file

        Returns:
            Path to converted PCM file or None on error
        """
        output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir='/tmp').name

        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-ar', '16000',      # 16kHz sample rate
            '-ac', '1',          # Mono
            '-f', 's16le',       # Raw PCM 16-bit little-endian
            '-acodec', 'pcm_s16le',
            output_path
        ]

        try:
            logging.info(f"Converting to PCM: {input_path} -> {output_path}")
            subprocess.run(
                ffmpeg_command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.FFMPEG_TIMEOUT
            )
            logging.info(f"PCM conversion successful")
            return output_path

        except subprocess.TimeoutExpired:
            logging.error(f"PCM conversion timed out")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None

        except subprocess.CalledProcessError as e:
            logging.error(f"PCM conversion failed: {e.stderr}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None

    def _transcribe_with_fallback(self, audio_path: str, language: str = 'ru') -> str:
        """
        Fallback transcription method using OpenAI if primary backend fails.

        Args:
            audio_path: Path to audio file
            language: Language code

        Returns:
            Transcribed text
        """
        if self.openai_client:
            logging.info("Falling back to OpenAI Whisper...")
            return self.transcribe_with_openai(audio_path, language)
        else:
            raise RuntimeError("No fallback transcription method available (OpenAI client not initialized)")

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

        # Get Whisper model path from environment
        model_path = os.getenv('WHISPER_MODEL_PATH', '/opt/whisper/models/ggml-base.bin')

        # Temporary output file for transcription
        output_json = f"{audio_path}.transcript.json"

        # Calculate timeout before try block to avoid unbound variable
        audio_duration = self.get_audio_duration(audio_path)
        timeout = max(int(audio_duration * 3), 60)

        try:
            # FFmpeg command with Whisper filter
            ffmpeg_command = [
                'ffmpeg',
                '-hide_banner',
                '-nostats',
                '-i', audio_path,
                '-vn',
                '-af', (
                    f"whisper="
                    f"model={model_path}:"
                    f"language={language}:"
                    f"format=json:"
                    f"use_gpu=false:"
                    f"queue=6"
                ),
                '-f', 'null',
                '-'
            ]

            logging.info(f"Starting FFmpeg Whisper transcription (timeout: {timeout}s)")

            result = subprocess.run(
                ffmpeg_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True
            )

            # Parse output from stderr
            transcription_text = self._parse_ffmpeg_whisper_output(result.stderr)

            if not transcription_text or len(transcription_text.strip()) < 5:
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
            # Cleanup temporary files
            if os.path.exists(output_json):
                os.remove(output_json)

    def transcribe_with_openai(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe audio using OpenAI Whisper API.
        """
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
            
        with open(audio_path, "rb") as audio_file:
            transcript = self.openai_client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                language=language
            )
            
        return transcript.text

    def _parse_ffmpeg_whisper_output(self, ffmpeg_stderr: str) -> str:
        """
        Parse transcription text from FFmpeg stderr output.
        Robustly extracts JSON objects from mixed log stream using stack-based parsing.
        
        Args:
            ffmpeg_stderr: FFmpeg stderr output containing mixed logs and JSON objects
            
        Returns:
            Concatenated transcription text
        """
        import json
        import re
        
        extracted_texts = []
        stack = []
        start_index = -1
        
        # Scan through the string to find balanced braces {}
        # This handles nested braces correctly unlike simple regex
        for i, char in enumerate(ffmpeg_stderr):
            if char == '{':
                if not stack:
                    start_index = i
                stack.append(char)
            elif char == '}':
                if stack:
                    stack.pop()
                    if not stack:
                        # Found a potentially complete JSON block
                        json_str = ffmpeg_stderr[start_index:i+1]
                        try:
                            # Verify it's valid JSON
                            data = json.loads(json_str)
                            
                            # Check if it's a Whisper segment
                            # Structure usually: {"t0":..., "t1":..., "text": "..."}
                            if isinstance(data, dict) and 'text' in data:
                                extracted_texts.append(data['text'].strip())
                        except json.JSONDecodeError:
                            # Not valid JSON (e.g. log text that happened to have balanced braces), ignore
                            pass
        
        if extracted_texts:
            return ' '.join(extracted_texts).strip()
            
        # Fallback: if JSON parsing completely failed, try regex for raw text (legacy format)
        logging.warning("No JSON segments found in Whisper output, attempting legacy parse")
        
        # Pattern: [whisper @ 0x...] text OR [Parsed_whisper_X @ 0x...] text
        # We need to be careful NOT to match the "run transcription at..." logs
        pattern = r'\[(?:Parsed_)?whisper(?:_\d+)? @ 0x[0-9a-f]+\]\s+(?!run transcription|audio:)(.+)'
        matches = re.findall(pattern, ffmpeg_stderr)
        
        if matches:
            # Filter out empty or too short matches that might be noise
            valid_matches = [m.strip() for m in matches if len(m.strip()) > 1]
            if valid_matches:
                return ' '.join(valid_matches).strip()
            
        # Debug: Log what we got if everything failed
        logging.error("Failed to parse Whisper output from FFmpeg.")
        # Log the last part of stderr which is more likely to contain errors
        logging.error(f"Last 1000 chars of stderr: {ffmpeg_stderr[-1000:]}")
        
        # Check for specific FFmpeg/Whisper error patterns in the whole stderr
        if "out of memory" in ffmpeg_stderr.lower():
            raise MemoryError("FFmpeg ran out of memory during transcription")
        if "segmentation fault" in ffmpeg_stderr.lower():
            raise RuntimeError("FFmpeg crashed (Segmentation Fault)")
        if "blank audio" in ffmpeg_stderr.lower() or "continuation follows" in ffmpeg_stderr.lower():
            return "Продолжение следует..." # Signal for blank audio
            
        return ""

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
            
    def format_text_with_qwen(self, text: str, use_code_tags: bool = False, use_yo: bool = True) -> str:
        """
        Format transcribed text using Qwen LLM (Alibaba) via REST API.
        Falls back to Gemini if Qwen fails.

        Args:
            text: Raw transcribed text
            use_code_tags: Whether to wrap text in <code> tags
            use_yo: Whether to preserve ё letters

        Returns:
            Formatted text
        """
        import requests

        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for LLM formatting ({word_count} words < 10), returning original")
            return text

        # Prepare instructions (same as Gemini for consistency)
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

        # EXACT SAME PROMPT as Gemini (proven quality)
        prompt = f"""Отформатируй транскрипцию аудиозаписи. Правила:

1. Исправь ошибки распознавания речи (артефакты, повторы, обрывки слов)
2. Расставь знаки препинания по правилам русского языка
3. НЕ заменяй слова на синонимы, НЕ меняй формы слов — сохраняй именно те слова, которые произнёс автор
4. Раздели на абзацы по смыслу и интонации (минимум 2-3 предложения в абзаце, не разбивай каждое предложение отдельно)
5. {code_tag_instruction}
6. {yo_instruction}
7. ВАЖНО: НЕ добавляй свои комментарии, НЕ веди диалог с пользователем

Текст для форматирования:

{text}"""

        api_start_time = time.time()

        try:
            api_key = self.alibaba_api_key or os.environ.get('DASHSCOPE_API_KEY')
            if not api_key:
                logging.warning("DASHSCOPE_API_KEY not set, falling back to Gemini")
                return self.format_text_with_gemini(text, use_code_tags, use_yo)

            logging.info(f"Starting Qwen LLM request via REST. Input chars: {len(text)}")

            # DashScope Qwen API via REST
            url = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "qwen-turbo",  # v3.0.1: qwen-turbo is 2x faster, 3x cheaper than qwen-plus
                "input": {
                    "messages": [
                        {"role": "user", "content": prompt}
                    ]
                },
                "parameters": {}
            }

            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                data = response.json()
                logging.info(f"Qwen response: {data}")

                # Extract text from response
                formatted_text = ""
                if 'output' in data:
                    output = data['output']
                    if isinstance(output, dict):
                        if 'text' in output:
                            formatted_text = output['text']
                        elif 'choices' in output:
                            choices = output['choices']
                            if choices and isinstance(choices, list):
                                formatted_text = choices[0].get('message', {}).get('content', '')

                formatted_text = formatted_text.strip()
                api_duration = time.time() - api_start_time
                logging.info(f"Qwen LLM finished. Duration: {api_duration:.2f}s, Output chars: {len(formatted_text)}")

                # Remove code tags if present but not wanted
                if not use_code_tags and formatted_text.startswith('<code>'):
                    formatted_text = formatted_text.replace('<code>', '').replace('</code>', '')

                # Quality check
                if len(formatted_text) < 5:
                    logging.warning("Qwen returned very short text, trying Gemini fallback")
                    return self.format_text_with_gemini(text, use_code_tags, use_yo)

                # Log API call metrics
                if self.metrics_service:
                    self.metrics_service.log_api_call('qwen-llm', api_duration, True)

                logging.info("Successfully formatted text with Qwen LLM")
                return formatted_text
            else:
                logging.warning(f"Qwen API error: {response.status_code} - {response.text}, trying Gemini fallback")
                return self.format_text_with_gemini(text, use_code_tags, use_yo)

        except requests.RequestException as e:
            logging.warning(f"Qwen API request failed: {e}, falling back to Gemini")
            return self.format_text_with_gemini(text, use_code_tags, use_yo)

        except Exception as e:
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('qwen-llm', api_duration, False, str(e))

            logging.warning(f"Qwen LLM failed: {e}, falling back to Gemini")
            return self.format_text_with_gemini(text, use_code_tags, use_yo)

    def format_text_with_gemini(self, text: str, use_code_tags: bool = False, use_yo: bool = True) -> str:
        """
        Format transcribed text using Gemini via HTTP API.
        Fallback method when Qwen LLM is unavailable.

        NOTE: This is the backup formatter. Requires GCP credentials.
        """
        import requests

        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for formatting ({word_count} words), returning original")
            return text

        api_start_time = time.time()

        try:
            # Get API key from environment
            api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
            if not api_key:
                logging.warning("No Gemini API key available, returning original text")
                return text

            # Prepare instructions
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

            prompt = f"""Отформатируй транскрипцию аудиозаписи. Правила:

1. Исправь ошибки распознавания речи (артефакты, повторы, обрывки слов)
2. Расставь знаки препинания по правилам русского языка
3. НЕ заменяй слова на синонимы, НЕ меняй формы слов — сохраняй именно те слова, которые произнёс автор
4. Раздели на абзацы по смыслу и интонации (минимум 2-3 предложения в абзаце, не разбивай каждое предложение отдельно)
5. {code_tag_instruction}
6. {yo_instruction}
7. ВАЖНО: НЕ добавляй свои комментарии, НЕ веди диалог с пользователем

Текст для форматирования:

{text}"""

            # Call Gemini API via REST
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "topP": 0.95,
                    "maxOutputTokens": 8192
                }
            }

            logging.info(f"Starting Gemini request. Input chars: {len(text)}")

            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()

            data = response.json()
            formatted_text = data['candidates'][0]['content']['parts'][0]['text'].strip()

            api_duration = time.time() - api_start_time
            logging.info(f"Gemini finished. Duration: {api_duration:.2f}s, Output chars: {len(formatted_text)}")

            # Remove code tags if present but not wanted
            if not use_code_tags and formatted_text.startswith('<code>'):
                formatted_text = formatted_text.replace('<code>', '').replace('</code>', '')

            # Quality check
            if len(formatted_text) < 5:
                logging.warning("Gemini returned very short text, using original")
                return text

            # Log API call metrics
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, True)

            logging.info("Successfully formatted text with Gemini")
            return formatted_text

        except Exception as e:
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('gemini', api_duration, False, str(e))

            logging.error(f"Gemini formatting failed: {str(e)}")
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
                
            # Format (Qwen LLM with Gemini fallback)
            formatted_text = self.format_text_with_qwen(transcribed_text)
            
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