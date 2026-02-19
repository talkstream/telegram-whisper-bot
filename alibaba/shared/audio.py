"""
Audio Service - Centralized audio processing operations for the Telegram Whisper Bot

v3.0.0: Alibaba Cloud migration - Qwen LLM for formatting with Gemini fallback
v2.1.0: Added Alibaba ASR backend option (requires OSS for local files)
v2.0.0: Added faster-whisper GPU support as alternative backend
"""
import os
import json
import logging
import tempfile
import subprocess
import time
import uuid
import base64
from typing import List, Optional, Tuple


class AudioService:
    """Service for all audio processing operations"""

    # Audio processing constants
    AUDIO_BITRATE = '48k'  # v3.5.0: increased from 32k for better ASR quality
    AUDIO_SAMPLE_RATE = '16000'  # 16kHz for ASR compatibility
    AUDIO_CHANNELS = '1'  # Mono for memory efficiency
    FFMPEG_THREADS = '4'  # Optimized for speed (was 1)
    FFMPEG_TIMEOUT = 60  # seconds

    # File size limits
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    MAX_DURATION_SECONDS = 3600  # 1 hour

    # ASR chunking limits (DashScope API hard limit: 3 min per request)
    ASR_MAX_DURATION = 180        # 3 min — DashScope API hard limit
    ASR_MAX_CHUNK_DURATION = 150  # 2.5 min — safe chunk size with margin

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
        self._diarization_debug = {}  # Debug info for admin diagnostics

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
        
    # Adaptive bitrate tiers for ASR (v3.5.0)
    # At 32kbps 16kHz mono MP3: ~87 min fits in 20MB (Telegram limit)
    # At 48kbps 16kHz mono MP3: ~58 min fits in 20MB
    # Below 32kbps ASR quality degrades significantly
    BITRATE_TIERS = [
        # (max_duration_sec, bitrate, sample_rate, label)
        (10,   '24k', '16000', 'ultra-light'),  # <10s: fastest processing
        (600,  '48k', '16000', 'standard'),      # <10min: high quality
        (1800, '32k', '16000', 'compressed'),     # <30min: good balance, ~82 min in 20MB
        (3600, '32k', '16000', 'compressed'),     # <60min: same bitrate, chunking handles ASR limit
    ]

    def _select_bitrate(self, duration: float) -> tuple:
        """Select optimal bitrate/sample_rate based on audio duration."""
        for max_dur, bitrate, sample_rate, label in self.BITRATE_TIERS:
            if duration <= max_dur:
                return bitrate, sample_rate, label
        # Fallback for very long files: minimum acceptable for ASR
        return '32k', '16000', 'compressed'

    @staticmethod
    def _safe_callback(callback, *args):
        """Invoke progress callback, swallowing errors so transcription continues."""
        if not callback:
            return
        try:
            callback(*args)
        except Exception as e:
            logging.warning(f"Progress callback failed: {e}")

    def convert_to_mp3(self, input_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Convert audio file to MP3 with adaptive settings based on duration.
        Automatically adjusts bitrate to optimize for ASR quality vs file size.
        Returns path to converted file or None on error.
        """
        if not output_path:
            output_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name

        duration = self.get_audio_duration(input_path)
        bitrate, sample_rate, tier = self._select_bitrate(duration)
        logging.info(f"Audio {duration:.1f}s - tier '{tier}': {bitrate} @ {sample_rate}Hz")

        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vn',                    # strip video/artwork (M4A from iOS often has cover art)
            '-acodec', 'libmp3lame',  # explicit MP3 codec
            '-b:a', bitrate,
            '-ar', sample_rate,
            '-ac', self.AUDIO_CHANNELS,
            '-threads', self.FFMPEG_THREADS,
            output_path
        ]

        try:
            logging.info(f"Converting audio: {input_path} -> {output_path}")
            subprocess.run(
                ffmpeg_command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.FFMPEG_TIMEOUT
            )

            output_size = os.path.getsize(output_path)
            logging.info(f"FFmpeg conversion successful. Output: {output_path} ({output_size} bytes)")
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
            
    # Allowed MIME types for audio processing (magic bytes detection)
    _ALLOWED_MIME_PREFIXES = ('audio/', 'video/', 'application/ogg', 'application/octet-stream')

    @staticmethod
    def _check_mime_type(file_path: str) -> bool:
        """Validate file MIME type using magic bytes. Returns True if allowed."""
        import mimetypes
        # Try python-magic first (most reliable), fallback to mimetypes
        try:
            import magic
            mime = magic.from_file(file_path, mime=True)
        except (ImportError, Exception):
            mime, _ = mimetypes.guess_type(file_path)
            if not mime:
                # Unknown extension — let FFmpeg decide
                return True

        for prefix in AudioService._ALLOWED_MIME_PREFIXES:
            if mime.startswith(prefix):
                return True
        logging.warning(f"MIME validation failed: {mime} for {file_path}")
        return False

    def prepare_audio_for_asr(self, input_path: str) -> Optional[str]:
        """
        Prepare audio file for ASR: validate MIME, detect video, extract audio, convert to MP3.

        Args:
            input_path: Path to input audio/video file

        Returns:
            Path to MP3 file ready for ASR, or None on error
        """
        if not self._check_mime_type(input_path):
            logging.error(f"File rejected: invalid MIME type for {input_path}")
            return None

        processing_path = input_path
        extracted_path = None
        try:
            if self.is_video_file(input_path):
                logging.info("Video detected, extracting audio...")
                extracted_path = self.extract_audio_from_video(input_path)
                if not extracted_path:
                    return None
                processing_path = extracted_path

            converted_path = self.convert_to_mp3(processing_path)
            return converted_path  # None if conversion failed
        except Exception as e:
            logging.error(f"Audio preparation failed: {e}")
            return None
        finally:
            # Always clean up intermediate extracted file
            if extracted_path and os.path.exists(extracted_path):
                try:
                    os.remove(extracted_path)
                except OSError:
                    pass

    def split_audio_chunks(self, audio_path: str, chunk_duration: int = None) -> list:
        """
        Split audio file into chunks for ASR processing.
        Uses FFmpeg stream copy (no re-encoding) for speed.

        Args:
            audio_path: Path to MP3 audio file
            chunk_duration: Chunk size in seconds (default: ASR_MAX_CHUNK_DURATION)

        Returns:
            List of chunk file paths. Returns [audio_path] if no splitting needed.
        """
        if chunk_duration is None:
            chunk_duration = self.ASR_MAX_CHUNK_DURATION

        total_duration = self.get_audio_duration(audio_path)
        if total_duration <= chunk_duration:
            return [audio_path]

        chunks = []
        offset = 0
        chunk_index = 0

        try:
            while offset < total_duration:
                chunk_path = tempfile.NamedTemporaryFile(
                    delete=False, suffix=f"_chunk{chunk_index}.mp3", dir='/tmp'
                ).name

                ffmpeg_command = [
                    'ffmpeg', '-y',
                    '-ss', str(offset),
                    '-i', audio_path,
                    '-t', str(chunk_duration),
                    '-acodec', 'copy',  # stream copy, no re-encoding
                    chunk_path
                ]

                subprocess.run(
                    ffmpeg_command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.FFMPEG_TIMEOUT
                )

                # Verify chunk is not empty
                if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                    chunks.append(chunk_path)
                else:
                    logging.warning(f"Chunk {chunk_index} is empty, skipping")

                offset += chunk_duration
                chunk_index += 1

            logging.info(f"Split audio into {len(chunks)} chunks ({total_duration:.0f}s total)")
            return chunks

        except Exception as e:
            logging.error(f"Audio splitting failed: {e}")
            # Clean up created chunks on error
            for chunk in chunks:
                if os.path.exists(chunk):
                    os.remove(chunk)
            return [audio_path]  # Fallback: try with original file

    def transcribe_audio(self, audio_path: str, language: str = 'ru',
                         progress_callback=None) -> str:
        """
        Transcribe audio file using configured backend.

        Args:
            audio_path: Path to audio file
            language: Language code (default: 'ru' for Russian)
            progress_callback: Optional callback(current_chunk, total_chunks) for progress

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
            return self.transcribe_with_qwen_asr(audio_path, language, progress_callback)
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

            # Force HTTPS for signed URLs (DashScope async ASR requires HTTPS)
            if endpoint and not endpoint.startswith('http'):
                endpoint = f'https://{endpoint}'

            access_key_id = self.oss_config.get('access_key_id')
            access_key_secret = self.oss_config.get('access_key_secret')

            if not all([bucket_name, endpoint, access_key_id, access_key_secret]):
                logging.warning("Incomplete OSS configuration")
                return None

            security_token = self.oss_config.get('security_token')
            if security_token:
                auth = oss2.StsAuth(access_key_id, access_key_secret, security_token)
            else:
                auth = oss2.Auth(access_key_id, access_key_secret)
            self._oss_bucket = oss2.Bucket(auth, endpoint, bucket_name)
            logging.info(f"OSS bucket initialized: {bucket_name}")
            return self._oss_bucket

        except ImportError:
            logging.warning("oss2 not installed")
            return None
        except Exception as e:
            logging.warning(f"Failed to initialize OSS bucket: {e}")
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

            # Upload file with retry on transient failures
            logging.info(f"Uploading to OSS: {local_path} -> {oss_key}")
            last_err = None
            for attempt in range(3):
                try:
                    bucket.put_object_from_file(oss_key, local_path)
                    last_err = None
                    break
                except Exception as upload_err:
                    last_err = upload_err
                    logging.warning(f"OSS upload attempt {attempt + 1}/3 failed: {upload_err}")
                    if attempt < 2:
                        time.sleep(1 << attempt)  # 1s, 2s
            if last_err:
                raise last_err

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

    def _upload_to_oss_with_url(self, local_path: str, expiry: int = 3600) -> Tuple[Optional[str], Optional[str]]:
        """Upload to OSS and return (oss_key, signed_https_url) for async ASR.

        Async ASR APIs require an HTTPS URL (not oss:// protocol).

        Args:
            local_path: Path to local file
            expiry: URL expiration in seconds (default: 1 hour)

        Returns:
            (oss_key, signed_url) or (None, None) on failure
        """
        bucket = self._get_oss_bucket()
        if not bucket:
            return None, None

        try:
            file_ext = os.path.splitext(local_path)[1] or '.mp3'
            oss_key = f"diarization/{uuid.uuid4().hex}{file_ext}"
            # Retry on transient failures
            last_err = None
            for attempt in range(3):
                try:
                    bucket.put_object_from_file(oss_key, local_path)
                    last_err = None
                    break
                except Exception as upload_err:
                    last_err = upload_err
                    logging.warning(f"OSS diarization upload attempt {attempt + 1}/3 failed: {upload_err}")
                    if attempt < 2:
                        time.sleep(1 << attempt)  # 1s, 2s
            if last_err:
                raise last_err
            signed_url = bucket.sign_url('GET', oss_key, expiry)
            logging.info(f"Uploaded to OSS for diarization: {oss_key}")
            return oss_key, signed_url
        except Exception as e:
            logging.warning(f"OSS upload for diarization failed: {e}")
            return None, None

    def _diarize_assemblyai(self, audio_path: str, language: str = 'ru',
                            progress_callback=None) -> Tuple[Optional[str], List[dict]]:
        """Diarization via AssemblyAI Universal-2.

        Workflow: upload file → submit transcription → poll until complete → parse utterances.
        Returns (raw_text, segments) or (None, []) on failure.
        """
        import requests as req

        self._diarization_debug = {'backend': 'assemblyai'}

        api_key = os.environ.get('ASSEMBLYAI_API_KEY')
        if not api_key:
            logging.warning("ASSEMBLYAI_API_KEY not configured")
            self._diarization_debug['error'] = 'no_api_key'
            return None, []
        headers = {'Authorization': api_key}

        try:
            self._safe_callback(progress_callback, "\U0001f504 Загружаю аудио в AssemblyAI...")

            # Step 1: Upload file
            with open(audio_path, 'rb') as f:
                upload_resp = req.post(
                    'https://api.assemblyai.com/v2/upload',
                    headers=headers, data=f, timeout=60)
            if upload_resp.status_code != 200:
                logging.warning(f"AssemblyAI upload failed: {upload_resp.status_code}")
                self._diarization_debug['error'] = f'upload_failed:{upload_resp.status_code}'
                return None, []
            upload_url = upload_resp.json()['upload_url']

            self._safe_callback(progress_callback, "\U0001f504 Распознаю с диаризацией (AssemblyAI)...")

            # Step 2: Submit transcription
            submit_resp = req.post(
                'https://api.assemblyai.com/v2/transcript',
                headers={**headers, 'Content-Type': 'application/json'},
                json={
                    'audio_url': upload_url,
                    'speech_models': ['universal-2'],
                    'speaker_labels': True,
                    'language_code': language,
                },
                timeout=30)
            if submit_resp.status_code != 200:
                error_data = submit_resp.json() if submit_resp.text else {}
                logging.warning(f"AssemblyAI submit failed: {submit_resp.status_code} - {error_data}")
                self._diarization_debug['error'] = f'submit_failed:{submit_resp.status_code}'
                return None, []
            transcript_id = submit_resp.json()['id']
            self._diarization_debug['transcript_id'] = transcript_id

            # Step 3: Poll until completed (exponential backoff: 1s → 2s → 4s → ... → 15s cap)
            # Wall-clock deadline: 240s leaves 60s headroom for upload/submit/delivery within FC 300s limit
            poll_url = f'https://api.assemblyai.com/v2/transcript/{transcript_id}'
            poll_delay = 1
            max_poll_delay = 15
            max_wait = 240
            deadline = time.monotonic() + max_wait
            while time.monotonic() < deadline:
                time.sleep(poll_delay)
                poll_resp = req.get(poll_url, headers=headers, timeout=15)
                data = poll_resp.json()
                status = data.get('status')
                if status == 'completed':
                    break
                if status == 'error':
                    logging.warning(f"AssemblyAI error: {data.get('error')}")
                    self._diarization_debug['error'] = f'transcription_error:{data.get("error")}'
                    return None, []
                poll_delay = min(poll_delay * 2, max_poll_delay)
            else:
                logging.warning(f"AssemblyAI polling timeout after {max_wait}s")
                self._diarization_debug['error'] = 'polling_timeout'
                return None, []

            # Step 4: Parse utterances
            raw_text = data.get('text', '')
            utterances = data.get('utterances', [])
            speaker_ids = {}
            segments = []
            for utt in utterances:
                speaker_label = utt.get('speaker', 'A')
                if speaker_label not in speaker_ids:
                    speaker_ids[speaker_label] = len(speaker_ids)
                segments.append({
                    'speaker_id': speaker_ids[speaker_label],
                    'text': utt.get('text', '').strip(),
                    'begin_time': utt.get('start', 0),
                    'end_time': utt.get('end', 0),
                })

            self._diarization_debug.update({
                'model': 'universal-2',
                'spk_segments': len(segments),
                'unique_speakers': len(speaker_ids),
                'fallback': 'none',
            })
            if segments:
                self._diarization_debug['merged_detail'] = '; '.join(
                    f"spk{s['speaker_id']}:{s['text'][:20]}" for s in segments[:8])

            return raw_text, segments

        except Exception as e:
            logging.warning(f"AssemblyAI diarization failed: {e}", exc_info=True)
            self._diarization_debug['fallback'] = f'exception: {e}'
            return None, []

    def _diarize_gemini(self, audio_path: str, language: str = 'ru',
                        progress_callback=None) -> Tuple[Optional[str], List[dict]]:
        """Diarization via Gemini 3 Flash with audio input and structured output.

        Sends base64-encoded audio to Gemini with a JSON schema for structured diarization output.
        Returns (raw_text, segments) or (None, []) on failure.
        """
        import requests as req

        api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
        if not api_key:
            logging.warning("GOOGLE_API_KEY not configured for diarization")
            return None, []

        self._diarization_debug = {'backend': 'gemini'}

        try:
            self._safe_callback(progress_callback, "\U0001f504 Распознаю с диаризацией (Gemini)...")

            # Read and base64 encode audio
            with open(audio_path, 'rb') as f:
                audio_b64 = base64.b64encode(f.read()).decode('utf-8')

            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-3-flash-preview:generateContent?key={api_key}")

            payload = {
                "contents": [{
                    "parts": [
                        {"inlineData": {"mimeType": "audio/mpeg", "data": audio_b64}},
                        {"text": (
                            "Transcribe this Russian audio conversation with speaker diarization. "
                            "Identify each unique speaker by voice and label them as \"1\", \"2\", \"3\", etc. "
                            "Return JSON with a \"segments\" array. Each segment has \"speaker\" (string) "
                            "and \"text\" (string). "
                            "Rules: transcribe in Russian, keep original words accurately, "
                            "each segment = one continuous speech from one speaker, "
                            "start new segment when speaker changes, do NOT merge different speakers."
                        )}
                    ]
                }],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "object",
                        "properties": {
                            "segments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "speaker": {"type": "string"},
                                        "text": {"type": "string"}
                                    },
                                    "required": ["speaker", "text"]
                                }
                            }
                        },
                        "required": ["segments"]
                    }
                }
            }

            response = req.post(url, json=payload, timeout=120)
            if response.status_code != 200:
                logging.warning(f"Gemini diarization failed: {response.status_code} "
                                f"{response.text[:200]}")
                return None, []

            # Parse structured JSON response
            result = response.json()
            candidates = result.get('candidates', [])
            if not candidates:
                logging.warning(f"Gemini diarization: empty candidates in response")
                return None, []
            text_content = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            parsed = json.loads(text_content)
            gemini_segments = parsed.get('segments', [])

            # Convert to our format
            speaker_ids = {}
            segments = []
            for seg in gemini_segments:
                speaker_label = str(seg.get('speaker', '1'))
                if speaker_label not in speaker_ids:
                    speaker_ids[speaker_label] = len(speaker_ids)
                text = seg.get('text', '').strip()
                if text:
                    segments.append({
                        'speaker_id': speaker_ids[speaker_label],
                        'text': text,
                        'begin_time': 0,
                        'end_time': 0,
                    })

            raw_text = ' '.join(s['text'] for s in segments)

            self._diarization_debug.update({
                'model': 'gemini-3-flash-preview',
                'spk_segments': len(segments),
                'unique_speakers': len(speaker_ids),
                'fallback': 'none',
            })
            if segments:
                self._diarization_debug['merged_detail'] = '; '.join(
                    f"spk{s['speaker_id']}:{s['text'][:20]}" for s in segments[:8])

            return raw_text, segments

        except Exception as e:
            logging.warning(f"Gemini diarization failed: {e}", exc_info=True)
            self._diarization_debug['fallback'] = f'exception: {e}'
            return None, []

    def transcribe_with_diarization(self, audio_path: str, language: str = 'ru',
                                     speaker_count: int = 0,
                                     progress_callback=None) -> Tuple[Optional[str], List[dict]]:
        """Diarization with configurable backend (DIARIZATION_BACKEND env var).

        Backends:
          - 'assemblyai': AssemblyAI Universal-3-Pro (best Russian diarization)
          - 'gemini': Gemini 3 Flash with structured output
          - 'dashscope' (default): fun-asr-mtl + qwen3-asr-flash-filetrans two-pass

        All backends return the same format. If selected backend fails,
        falls back to dashscope automatically.

        Args:
            audio_path: Path to audio file
            language: Language code (default: 'ru')
            speaker_count: Expected number of speakers (0 = auto-detect)
            progress_callback: Optional callback(stage_text) for progress updates

        Returns:
            (raw_text, segments) where segments = [{'speaker_id', 'text', 'begin_time', 'end_time'}]
            Returns (None, []) on failure (caller should fallback to regular ASR)
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

        self._diarization_debug = {}  # Reset for each call

        # Backend routing
        backend = os.environ.get('DIARIZATION_BACKEND', 'dashscope')
        logging.info(f"Diarization backend: {backend}")

        if backend == 'assemblyai':
            result = self._diarize_assemblyai(audio_path, language,
                                               progress_callback=progress_callback)
            if result[1]:  # segments not empty
                return result
            logging.warning("AssemblyAI returned no segments, falling back to dashscope")
            # Snapshot AssemblyAI debug, reset for DashScope
            self._diarization_debug = {
                'attempted_backend': 'assemblyai',
                'attempted_debug': dict(self._diarization_debug),
            }

        elif backend == 'gemini':
            result = self._diarize_gemini(audio_path, language,
                                           progress_callback=progress_callback)
            if result[1]:
                return result
            logging.warning("Gemini returned no segments, falling back to dashscope")
            # Snapshot Gemini debug, reset for DashScope
            self._diarization_debug = {
                'attempted_backend': 'gemini',
                'attempted_debug': dict(self._diarization_debug),
            }

        # Default: DashScope two-pass diarization
        api_key = self.alibaba_api_key or os.environ.get('DASHSCOPE_API_KEY')
        if not api_key:
            logging.warning("DASHSCOPE_API_KEY not configured for diarization")
            return None, []

        oss_key = None
        try:
            # Step 1: Upload to OSS (once, shared by both passes)
            oss_key, signed_url = self._upload_to_oss_with_url(audio_path)
            if not signed_url:
                logging.warning("Failed to upload to OSS for diarization")
                return None, []

            self._safe_callback(progress_callback, "\U0001f504 Распознаю с диаризацией...")

            # Step 2: Launch both passes in parallel
            spk_params: dict = {'diarization_enabled': True}
            if speaker_count > 0:
                spk_params['speaker_count'] = speaker_count

            txt_params = {'language': language, 'enable_words': True}

            spk_result = None
            txt_result = None

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_spk = executor.submit(
                    self._submit_async_transcription,
                    signed_url, 'fun-asr-mtl', spk_params, api_key,
                    debug_prefix='pass1')
                future_txt = executor.submit(
                    self._submit_async_transcription,
                    signed_url, 'qwen3-asr-flash-filetrans', txt_params, api_key,
                    debug_prefix='pass2')

                # Process results as they complete (270s timeout for FC headroom)
                future_map = {future_spk: 'pass1', future_txt: 'pass2'}
                try:
                    for future in as_completed(future_map, timeout=270):
                        label = future_map[future]
                        try:
                            result = future.result()
                            if label == 'pass1':
                                spk_result = result
                            else:
                                txt_result = result
                        except Exception as e:
                            if label == 'pass1':
                                logging.warning(f"Pass 1 (fun-asr-mtl) failed: {e}")
                            else:
                                logging.warning(f"Pass 2 (qwen3-asr-flash-filetrans) failed: {e}")
                except FuturesTimeoutError:
                    logging.error("Diarization passes timed out after 270s")

            self._safe_callback(progress_callback, "\U0001f504 Объединяю результаты...")

            # Step 3: Parse results
            speaker_segments = self._parse_speaker_segments(spk_result) if spk_result else []
            text_segments = self._parse_text_segments(txt_result) if txt_result else []

            self._diarization_debug['spk_segments'] = len(speaker_segments)
            self._diarization_debug['txt_segments'] = len(text_segments)

            # Debug: segment details (first 5 of each)
            if speaker_segments:
                self._diarization_debug['spk_detail'] = '; '.join(
                    f"spk{s['speaker_id']}[{s['begin_time']}-{s['end_time']}]"
                    for s in speaker_segments[:5]
                )
            if text_segments:
                is_word_level = len(text_segments) > 20
                sample = 10 if is_word_level else 5
                self._diarization_debug['txt_detail'] = '; '.join(
                    f"[{s['begin_time']}-{s['end_time']}]{s['text'][:20]}"
                    for s in text_segments[:sample]
                )
                if is_word_level:
                    self._diarization_debug['txt_word_level'] = True

            # Fallback cascade
            if not text_segments and not speaker_segments:
                logging.warning("Both diarization passes returned no data")
                self._diarization_debug['fallback'] = 'both_empty'
                return None, []

            if not text_segments:
                # Pass 2 failed — use Pass 1 text (wrong language but has speakers)
                logging.warning("Pass 2 failed, using Pass 1 text (may be inaccurate)")
                self._diarization_debug['fallback'] = 'pass2_failed_using_pass1_text'
                raw_texts = [s['text'] for s in speaker_segments if s.get('text')]
                raw_text = ' '.join(raw_texts) if raw_texts else None
                return raw_text, speaker_segments

            if not speaker_segments:
                # Pass 1 failed — return text without speaker labels
                logging.warning("Pass 1 failed, returning text without speaker labels")
                self._diarization_debug['fallback'] = 'pass1_failed_no_speakers'
                raw_texts = [s['text'] for s in text_segments if s.get('text')]
                raw_text = ' '.join(raw_texts) if raw_texts else None
                return raw_text, []

            # Step 4: Merge — align speaker labels with accurate text
            merged = self._align_speakers_with_text(speaker_segments, text_segments)

            # Debug: merged segment details (first 8)
            self._diarization_debug['merged_detail'] = '; '.join(
                f"spk{s['speaker_id']}:{s['text'][:20]}"
                for s in merged[:8]
            )

            raw_texts = [s['text'] for s in merged if s.get('text')]
            raw_text = ' '.join(raw_texts)

            self._diarization_debug['fallback'] = 'none'
            logging.info(f"Two-pass diarization: {len(merged)} segments, "
                         f"{len(set(s['speaker_id'] for s in merged))} speakers, "
                         f"{len(raw_text)} chars")

            return raw_text, merged

        except Exception as e:
            logging.warning(f"Diarization failed: {e}", exc_info=True)
            self._diarization_debug['fallback'] = f'exception: {e}'
            return None, []
        finally:
            self._cleanup_oss_key(oss_key)

    def _parse_speaker_segments(self, trans_data: dict) -> List[dict]:
        """Parse fun-asr-mtl transcription result into speaker segments."""
        segments = []
        for transcript in trans_data.get('transcripts', []):
            for sentence in transcript.get('sentences', []):
                seg = {
                    'speaker_id': sentence.get('speaker_id', 0),
                    'text': sentence.get('text', '').strip(),
                    'begin_time': sentence.get('begin_time', 0),
                    'end_time': sentence.get('end_time', 0)
                }
                if seg['begin_time'] is not None and seg['end_time'] is not None:
                    segments.append(seg)
        return segments

    def _parse_text_segments(self, trans_data: dict) -> List[dict]:
        """Parse qwen3-asr-flash-filetrans result into text segments.

        When enable_words=true, uses word-level data for precise timestamps.
        Each word becomes its own segment. Falls back to sentence-level if
        words array is absent.
        """
        segments = []
        for transcript in trans_data.get('transcripts', []):
            for sentence in transcript.get('sentences', []):
                words = sentence.get('words')
                if words:
                    for w in words:
                        text = (w.get('text', '') + w.get('punctuation', '')).strip()
                        if text:
                            segments.append({
                                'text': text,
                                'begin_time': w.get('begin_time', 0),
                                'end_time': w.get('end_time', 0)
                            })
                else:
                    text = sentence.get('text', '').strip()
                    if text:
                        segments.append({
                            'text': text,
                            'begin_time': sentence.get('begin_time', 0),
                            'end_time': sentence.get('end_time', 0)
                        })
        return segments

    def _cleanup_oss_key(self, oss_key: Optional[str]):
        """Delete an OSS object by key."""
        if not oss_key:
            return
        bucket = self._get_oss_bucket()
        if bucket:
            try:
                bucket.delete_object(oss_key)
                logging.info(f"Deleted from OSS: {oss_key}")
            except Exception as e:
                logging.warning(f"Failed to delete OSS key {oss_key}: {e}")

    def _submit_async_transcription(self, signed_url: str, model: str,
                                      params: dict, api_key: str,
                                      poll_interval: int = 5,
                                      max_wait: int = 240,
                                      debug_prefix: str = "") -> Optional[dict]:
        """Submit an async transcription job and poll until completion.

        Handles difference in input format:
        - fun-asr-mtl: {"file_urls": [url]}  (plural, list)
        - qwen3-asr-flash-filetrans: {"file_url": url}  (singular, string)

        Args:
            signed_url: HTTPS URL to the audio file
            model: Model name (fun-asr-mtl or qwen3-asr-flash-filetrans)
            params: Parameters dict (language_hints, diarization_enabled, etc.)
            api_key: DashScope API key
            poll_interval: Seconds between polls (default: 5)
            max_wait: Maximum wait time in seconds (default: 240)
            debug_prefix: Prefix for debug keys in _diarization_debug (e.g. 'pass1', 'pass2')

        Returns:
            Parsed transcription data dict, or None on failure
        """
        import requests

        pfx = debug_prefix  # shorthand

        url = "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable"
        }

        # Build input based on model
        if model == 'qwen3-asr-flash-filetrans':
            input_data = {"file_url": signed_url}
        else:
            input_data = {"file_urls": [signed_url]}

        payload = {
            "model": model,
            "input": input_data,
            "parameters": params
        }

        # Record request (strip signed URL query string for security)
        if pfx:
            safe_url = signed_url.split('?')[0] + '?...' if '?' in signed_url else signed_url
            req_repr = json.dumps({**payload, "input": {k: safe_url if 'url' in k else v
                                                         for k, v in input_data.items()}},
                                   ensure_ascii=False)[:800]
            self._diarization_debug[f'{pfx}_request'] = req_repr

        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if pfx:
            self._diarization_debug[f'{pfx}_submit_status'] = response.status_code

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            logging.warning(f"{model} submit failed: {response.status_code} - {error_data}")
            if pfx:
                self._diarization_debug[f'{pfx}_submit_body'] = str(error_data)[:500]
                self._diarization_debug[f'{pfx}_result'] = 'submit_failed'
            return None

        task_data = response.json()
        task_id = task_data.get('output', {}).get('task_id')
        if not task_id:
            logging.warning(f"{model} returned no task_id: {task_data}")
            if pfx:
                self._diarization_debug[f'{pfx}_submit_body'] = str(task_data)[:500]
                self._diarization_debug[f'{pfx}_result'] = 'submit_failed'
            return None

        if pfx:
            self._diarization_debug[f'{pfx}_task_id'] = task_id

        # Poll for completion
        poll_url = f"https://dashscope-intl.aliyuncs.com/api/v1/tasks/{task_id}"
        poll_headers = {"Authorization": f"Bearer {api_key}"}
        max_attempts = max_wait // max(poll_interval, 1)

        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            poll_response = requests.get(poll_url, headers=poll_headers, timeout=15)
            if poll_response.status_code != 200:
                continue

            poll_data = poll_response.json()
            task_status = poll_data.get('output', {}).get('task_status', '')

            if task_status == 'SUCCEEDED':
                logging.info(f"{model} task completed after {(attempt + 1) * poll_interval}s")
                break
            elif task_status == 'FAILED':
                error_msg = poll_data.get('output', {}).get('message', 'unknown')
                logging.warning(f"{model} task failed: {error_msg}")
                if pfx:
                    self._diarization_debug[f'{pfx}_poll_body'] = str(poll_data)[:500]
                    self._diarization_debug[f'{pfx}_result'] = f'task_failed: {error_msg}'
                return None
        else:
            logging.warning(f"{model} task timed out after {max_wait}s")
            if pfx:
                self._diarization_debug[f'{pfx}_result'] = f'timeout_{max_wait}s'
            return None

        # Fetch transcription results
        # DashScope returns different formats per model:
        #   fun-asr-mtl: output.results = [{transcription_url: ...}]
        #   qwen3-asr-flash-filetrans: output.result = {transcription_url: ...}
        output = poll_data.get('output', {})
        results = output.get('results', [])
        result_obj = output.get('result', {})

        if results:
            transcription_url = results[0].get('transcription_url')
        elif result_obj:
            transcription_url = result_obj.get('transcription_url')
        else:
            transcription_url = None

        if not transcription_url:
            logging.warning(f"{model} returned no transcription_url")
            if pfx:
                self._diarization_debug[f'{pfx}_poll_body'] = str(poll_data)[:500]
                self._diarization_debug[f'{pfx}_result'] = 'no_transcription_url'
            return None

        trans_response = requests.get(transcription_url, timeout=30)
        trans_data = trans_response.json()

        if pfx:
            self._diarization_debug[f'{pfx}_result'] = 'ok'
            self._diarization_debug[f'{pfx}_transcription_len'] = len(
                json.dumps(trans_data, ensure_ascii=False))

        return trans_data

    def _align_speakers_with_text(self, speaker_segments: List[dict],
                                    text_segments: List[dict]) -> List[dict]:
        """Align speaker labels with text using word-level timestamp estimation.

        Builds a word stream from text segments (linear interpolation for word times),
        then assigns each word to the speaker segment that contains its estimated time.
        Consecutive same-speaker words are merged into segments.

        Args:
            speaker_segments: [{'speaker_id', 'begin_time', 'end_time'}, ...]
                              from fun-asr-mtl (times in milliseconds)
            text_segments: [{'text', 'begin_time', 'end_time'}, ...]
                           from qwen3-asr-flash-filetrans (times in milliseconds)

        Returns:
            Merged list: [{'speaker_id', 'text', 'begin_time', 'end_time'}, ...]
        """
        if not speaker_segments or not text_segments:
            # No speaker info: assign all to speaker 0
            return [
                {'speaker_id': 0, 'text': seg['text'],
                 'begin_time': seg['begin_time'], 'end_time': seg['end_time']}
                for seg in text_segments
            ]

        # Normalize timelines: both models may report different total durations
        # for the same audio. Scale to [0.0, 1.0] range for alignment.
        spk_max = max(s['end_time'] for s in speaker_segments) or 1
        txt_max = max(s['end_time'] for s in text_segments) or 1

        if abs(spk_max - txt_max) / max(spk_max, txt_max) > 0.1:
            # >10% difference — normalize both to [0, 1] range
            logging.info(f"Diarization timeline mismatch: spk={spk_max}ms, txt={txt_max}ms, normalizing")
            self._diarization_debug['timeline_normalized'] = f'{spk_max}ms/{txt_max}ms'
            speaker_segments = [
                {**s, 'begin_time': s['begin_time'] / spk_max,
                       'end_time': s['end_time'] / spk_max}
                for s in speaker_segments
            ]
            text_segments = [
                {**s, 'begin_time': s['begin_time'] / txt_max,
                       'end_time': s['end_time'] / txt_max}
                for s in text_segments
            ]

        # Step 1: Build word stream with estimated timestamps
        word_stream = []  # [(estimated_time_ms, word), ...]
        for tseg in text_segments:
            words = tseg['text'].split()
            if not words:
                continue
            t_begin = tseg['begin_time']
            t_end = tseg['end_time']
            duration = max(t_end - t_begin, 1)
            for i, word in enumerate(words):
                word_time = t_begin + (i * duration) / len(words)
                word_stream.append((word_time, word))

        if not word_stream:
            return []

        # Step 2: Assign each word to a speaker
        # Use pointer into sorted speaker_segments for O(n+m)
        spk_idx = 0
        merged = []
        current_speaker = None
        current_words = []
        current_begin = 0
        current_end = 0

        for word_time, word in word_stream:
            # Advance pointer past speaker segments that ended before this word
            while (spk_idx < len(speaker_segments) - 1 and
                   speaker_segments[spk_idx]['end_time'] <= word_time):
                spk_idx += 1

            # Find best speaker: check nearby segments for overlap
            best_speaker = speaker_segments[0]['speaker_id']
            best_dist = float('inf')
            for i in range(max(0, spk_idx - 1),
                           min(spk_idx + 2, len(speaker_segments))):
                sseg = speaker_segments[i]
                if sseg['begin_time'] <= word_time < sseg['end_time']:
                    best_speaker = sseg['speaker_id']
                    best_dist = 0
                    break
                # In gap: use nearest segment boundary
                dist = min(abs(word_time - sseg['begin_time']),
                           abs(word_time - sseg['end_time']))
                if dist < best_dist:
                    best_dist = dist
                    best_speaker = sseg['speaker_id']

            # Accumulate or flush
            if best_speaker != current_speaker:
                if current_words:
                    merged.append({
                        'speaker_id': current_speaker,
                        'text': ' '.join(current_words),
                        'begin_time': current_begin,
                        'end_time': current_end
                    })
                current_speaker = best_speaker
                current_words = [word]
                current_begin = word_time
                current_end = word_time
            else:
                current_words.append(word)
                current_end = word_time

        if current_words:
            merged.append({
                'speaker_id': current_speaker,
                'text': ' '.join(current_words),
                'begin_time': current_begin,
                'end_time': current_end
            })

        return merged

    def format_dialogue(self, segments: List[dict],
                        show_speakers: bool = True) -> str:
        """Format diarized segments as Russian dialogue with em-dash.

        Merges consecutive segments from the same speaker into one block.
        Shows "Спикер N:" labels when 2+ speakers are present and
        show_speakers is True. Filters punctuation-only segments.
        Numbers speakers by first appearance.

        Args:
            segments: List of dicts with 'speaker_id' and 'text' keys
            show_speakers: Whether to include "Спикер N:" labels

        Returns:
            Formatted dialogue text with em-dash separators and speaker labels
        """
        import re

        # Filter punctuation-only segments (e.g. ".", ",", "...")
        segments = [seg for seg in segments
                    if re.search(r'[\w]', seg.get('text', ''))]

        # Map speaker IDs to sequential 1-based numbers by first appearance
        speaker_map = {}
        for seg in segments:
            sid = seg.get('speaker_id', 0)
            if sid not in speaker_map:
                speaker_map[sid] = len(speaker_map) + 1
        use_labels = len(speaker_map) > 1

        lines = []
        current_speaker = None
        current_texts = []

        for seg in segments:
            speaker = seg.get('speaker_id', 0)
            text = seg.get('text', '').strip()
            if not text:
                continue
            if speaker != current_speaker:
                if current_texts:
                    joined = '\n'.join(current_texts)
                    if use_labels and show_speakers:
                        label = f"Спикер {speaker_map.get(current_speaker, '?')}"
                        lines.append(f"{label}:\n\u2014 {joined}")
                    else:
                        lines.append(f"\u2014 {joined}")
                current_speaker = speaker
                current_texts = [text]
            else:
                current_texts.append(text)

        if current_texts:
            joined = '\n'.join(current_texts)
            if use_labels and show_speakers:
                label = f"Спикер {speaker_map.get(current_speaker, '?')}"
                lines.append(f"{label}:\n\u2014 {joined}")
            else:
                lines.append(f"\u2014 {joined}")

        return "\n".join(lines)

    def get_diarization_debug(self) -> Optional[str]:
        """Format _diarization_debug dict into a human-readable text block for admin.

        Returns:
            Formatted debug text (HTML-safe, <=3900 chars) or None if no debug data
        """
        import html

        dbg = self._diarization_debug
        if not dbg:
            return None

        lines = ["DIARIZATION DEBUG", ""]

        # Show attempted backend details if there was a fallback
        if 'attempted_debug' in dbg:
            ad = dbg['attempted_debug']
            name = dbg.get('attempted_backend', 'unknown').upper()
            lines.append(f"--- {name} (attempted, fell back) ---")
            if 'error' in ad:
                lines.append(f"error: {ad['error']}")
            if 'transcript_id' in ad:
                lines.append(f"transcript_id: {ad['transcript_id']}")
            lines.append(f"model: {ad.get('model', 'n/a')}")
            lines.append(f"segments: {ad.get('spk_segments', 0)}")
            if ad.get('fallback') and ad['fallback'] != 'none':
                lines.append(f"exception: {ad['fallback']}")
            lines.append("")

        # AssemblyAI / Gemini backends (successful, return early)
        if dbg.get('backend') in ('assemblyai', 'gemini'):
            lines.append(f"--- {dbg['backend'].upper()} ---")
            lines.append(f"model: {dbg.get('model', 'n/a')}")
            lines.append(f"segments: {dbg.get('spk_segments', 0)}")
            lines.append(f"speakers: {dbg.get('unique_speakers', 0)}")
            if 'transcript_id' in dbg:
                lines.append(f"transcript_id: {dbg['transcript_id']}")
            if 'merged_detail' in dbg:
                lines.append(f"detail: {dbg['merged_detail']}")
            if 'fallback' in dbg:
                lines.append(f"fallback: {dbg['fallback']}")
            text = html.escape('\n'.join(lines))
            return text[:3900]

        # DashScope two-pass
        # Pass 1
        lines.append("--- Pass 1 (fun-asr-mtl) ---")
        lines.append(f"result: {dbg.get('pass1_result', 'n/a')}")
        if 'pass1_submit_status' in dbg:
            lines.append(f"http: {dbg['pass1_submit_status']}")
        if 'pass1_task_id' in dbg:
            lines.append(f"task_id: {dbg['pass1_task_id']}")
        if 'pass1_transcription_len' in dbg:
            lines.append(f"transcription_len: {dbg['pass1_transcription_len']}")
        if 'pass1_submit_body' in dbg:
            lines.append(f"response: {dbg['pass1_submit_body']}")
        if 'pass1_poll_body' in dbg:
            lines.append(f"poll_response: {dbg['pass1_poll_body']}")
        if 'pass1_request' in dbg:
            lines.append(f"request: {dbg['pass1_request']}")

        lines.append("")

        # Pass 2
        lines.append("--- Pass 2 (qwen3-asr-flash-filetrans) ---")
        lines.append(f"result: {dbg.get('pass2_result', 'n/a')}")
        if 'pass2_submit_status' in dbg:
            lines.append(f"http: {dbg['pass2_submit_status']}")
        if 'pass2_task_id' in dbg:
            lines.append(f"task_id: {dbg['pass2_task_id']}")
        if 'pass2_transcription_len' in dbg:
            lines.append(f"transcription_len: {dbg['pass2_transcription_len']}")
        if 'pass2_submit_body' in dbg:
            lines.append(f"response: {dbg['pass2_submit_body']}")
        if 'pass2_poll_body' in dbg:
            lines.append(f"poll_response: {dbg['pass2_poll_body']}")
        if 'pass2_request' in dbg:
            lines.append(f"request: {dbg['pass2_request']}")

        lines.append("")

        # Merge stats
        lines.append("--- Merge ---")
        if 'spk_segments' in dbg:
            lines.append(f"spk_segments: {dbg['spk_segments']}")
        if 'txt_segments' in dbg:
            lines.append(f"txt_segments: {dbg['txt_segments']}")
        if dbg.get('txt_word_level'):
            lines.append("txt_mode: word-level")
        if 'timeline_normalized' in dbg:
            lines.append(f"timeline_normalized: {dbg['timeline_normalized']}")
        if 'spk_detail' in dbg:
            lines.append(f"spk_detail: {dbg['spk_detail']}")
        if 'txt_detail' in dbg:
            lines.append(f"txt_detail: {dbg['txt_detail']}")
        if 'merged_detail' in dbg:
            lines.append(f"merged: {dbg['merged_detail']}")
        if 'fallback' in dbg:
            lines.append(f"fallback: {dbg['fallback']}")

        text = html.escape('\n'.join(lines))
        return text[:3900]

    def transcribe_with_qwen_asr(self, audio_path: str, language: str = 'ru',
                                   progress_callback=None) -> str:
        """
        Transcribe audio using Qwen3-ASR-Flash. Auto-chunks if > ASR_MAX_CHUNK_DURATION.

        Args:
            audio_path: Path to audio file (MP3, WAV, etc.)
            language: Language code (default: 'ru' for Russian)
            progress_callback: Optional callback(current_chunk, total_chunks)

        Returns:
            Transcribed text
        """
        audio_duration = self.get_audio_duration(audio_path)
        if audio_duration > self.ASR_MAX_CHUNK_DURATION:
            logging.info(f"Audio {audio_duration:.0f}s exceeds chunk limit {self.ASR_MAX_CHUNK_DURATION}s, chunking...")
            return self._transcribe_chunked(audio_path, language, audio_duration, progress_callback)
        return self._transcribe_single_qwen_asr(audio_path, language)

    def _transcribe_chunked(self, audio_path: str, language: str,
                             audio_duration: float, progress_callback=None) -> str:
        """
        Transcribe long audio by splitting into chunks and concatenating results.

        Args:
            audio_path: Path to audio file
            language: Language code
            audio_duration: Total duration in seconds
            progress_callback: Optional callback(current_chunk, total_chunks)

        Returns:
            Concatenated transcribed text
        """
        chunks = self.split_audio_chunks(audio_path)
        total_chunks = len(chunks)
        logging.info(f"Transcribing {total_chunks} chunks for {audio_duration:.0f}s audio")

        texts = []
        failed_chunks = 0
        try:
            for i, chunk_path in enumerate(chunks):
                if total_chunks > 1:
                    self._safe_callback(progress_callback, i + 1, total_chunks)

                try:
                    text = self._transcribe_single_qwen_asr(chunk_path, language)
                    if text:
                        texts.append(text)
                except Exception as chunk_err:
                    failed_chunks += 1
                    logging.warning(f"Chunk {i+1}/{total_chunks} failed: {chunk_err}")
                    # Continue with remaining chunks instead of failing entirely
                    if failed_chunks > total_chunks // 2:
                        raise RuntimeError(
                            f"Too many chunks failed ({failed_chunks}/{total_chunks})")

            if not texts:
                raise ValueError("Transcription empty")

            full_text = " ".join(texts)
            if failed_chunks:
                logging.warning(
                    f"Chunked transcription partial: {len(texts)}/{total_chunks} chunks, "
                    f"{failed_chunks} failed, {len(full_text)} chars")
            else:
                logging.info(
                    f"Chunked transcription complete: {len(texts)} chunks, {len(full_text)} chars")
            return full_text

        finally:
            # Clean up chunk files (but not the original)
            for chunk_path in chunks:
                if chunk_path != audio_path and os.path.exists(chunk_path):
                    os.remove(chunk_path)

    def _transcribe_single_qwen_asr(self, audio_path: str, language: str = 'ru') -> str:
        """
        Transcribe a single audio segment using Qwen3-ASR-Flash via DashScope REST API.
        Must be <= 3 min (ASR_MAX_DURATION).

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
                        "enable_itn": True,
                        "language_hints": ["ru"]
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
            
    def _build_format_prompt(self, text: str, use_code_tags: bool, use_yo: bool,
                              is_chunked: bool, is_dialogue: bool) -> str:
        """Single source of truth for LLM formatting prompt."""
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

        extra_instructions = ""

        if is_chunked:
            extra_instructions += (
                "10. ВАЖНО: этот текст собран из нескольких последовательных фрагментов одной записи. "
                "На стыках фрагментов могут быть оборванные предложения, повторы слов или неестественные "
                "переходы — исправь эти артефакты склейки, обеспечив плавный непрерывный текст.\n"
            )

        if is_dialogue:
            extra_instructions += (
                "11. ФОРМАТ ДИАЛОГА: текст уже отформатирован как диалог с тире (\u2014). "
                "Некоторые реплики могут быть распределены с ошибками, поэтому осмысли весь диалог "
                "и внеси коррективы там, где ты точно уверен, что исправление требуется. "
                "Например, это касается спорных моментов с разрывом фраз одного и того же спикера. "
                "НЕ добавляй метки спикеров. НЕ меняй порядок реплик. "
                "НЕ добавляй пустые строки между репликами — каждая реплика начинается "
                "с новой строки сразу после предыдущей, без отступов. "
                "Правило 4 (абзацы) к диалогу НЕ применяется. "
                "Также исправь пунктуацию и артефакты ASR внутри каждого блока.\n"
            )
        else:
            extra_instructions += (
                "11. НЕ используй тире (\u2014) для оформления прямой речи. "
                "Оформляй как связный текст, разделённый на абзацы по смыслу.\n"
            )

        return f"""Отформатируй транскрипцию аудиозаписи. Правила:

1. Исправь ЯВНЫЕ ошибки распознавания речи (артефакты, повторы, обрывки слов)
2. Расставь знаки препинания по правилам русского языка
3. НЕ заменяй слова на синонимы, НЕ меняй формы слов — сохраняй именно те слова, которые произнёс автор
4. Раздели на абзацы по смыслу и интонации (минимум 2-3 предложения в абзаце, не разбивай каждое предложение отдельно)
5. {code_tag_instruction}
6. {yo_instruction}
7. ВАЖНО: НЕ добавляй свои комментарии, НЕ веди диалог с пользователем
8. ИМЕНА И ФАМИЛИИ: будь максимально консервативен с именами собственными. \
Если слово похоже на фамилию/имя — НЕ заменяй его на похожее. \
Не «исправляй» незнакомые фамилии на более распространённые.
9. ШИПЯЩИЕ/СВИСТЯЩИЕ: ASR часто путает ш/щ/ч/ж/с/з/ц. \
Исправляй только если результат явно не слово русского языка. \
В сомнительных случаях — оставляй как распознал ASR.
{extra_instructions}Обрати особое внимание на корректное написание топонимов (географических названий). \
Приоритет: топонимы Таиланда (Бангкок, Паттайя, Пхукет, Краби, Чиангмай, Ко Самуи, \
Ко Панган, Ко Чанг, Хуа Хин, Районг и т.д.), затем России. \
Не заменяй правильные топонимы на похожие по звучанию слова.

Текст для форматирования:

{text}"""

    def format_text_with_llm(self, text: str, use_code_tags: bool = False,
                              use_yo: bool = True, is_chunked: bool = False,
                              is_dialogue: bool = False,
                              backend: str | None = None) -> str:
        """Route LLM formatting to configured backend.

        Priority: backend param → LLM_BACKEND env var → 'qwen'.
        """
        backend = backend or os.environ.get('LLM_BACKEND', 'qwen')
        logging.info(f"LLM backend: {backend}")
        if backend == 'assemblyai':
            result = self.format_text_with_assemblyai(
                text, use_code_tags, use_yo, is_chunked, is_dialogue)
        else:  # 'qwen' default
            result = self.format_text_with_qwen(
                text, use_code_tags, use_yo, is_chunked, is_dialogue)

        # Fix spaced ellipsis from ASR artifacts (". . ." → "...")
        import re
        result = re.sub(r'(?:\.\s){2,}\.', '...', result)
        return result

    def format_text_with_qwen(self, text: str, use_code_tags: bool = False,
                               use_yo: bool = True, is_chunked: bool = False,
                               is_dialogue: bool = False,
                               _is_fallback: bool = False) -> str:
        """
        Format transcribed text using Qwen LLM (Alibaba) via REST API.
        Falls back to AssemblyAI LLM Gateway if Qwen fails.

        Args:
            text: Raw transcribed text
            use_code_tags: Whether to wrap text in <code> tags
            use_yo: Whether to preserve ё letters
            is_chunked: Whether text was assembled from multiple ASR chunks
            is_dialogue: Whether text is a multi-speaker dialogue
            _is_fallback: Whether this is already a fallback call (prevents loops)

        Returns:
            Formatted text
        """
        import requests

        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for LLM formatting ({word_count} words < 10), returning original")
            return text

        prompt = self._build_format_prompt(text, use_code_tags, use_yo, is_chunked, is_dialogue)

        api_start_time = time.time()

        try:
            api_key = self.alibaba_api_key or os.environ.get('DASHSCOPE_API_KEY')
            if not api_key:
                if _is_fallback:
                    logging.warning("DASHSCOPE_API_KEY not set, returning original text")
                    return text
                logging.warning("DASHSCOPE_API_KEY not set, falling back to AssemblyAI")
                return self.format_text_with_assemblyai(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

            logging.info(f"Starting Qwen LLM request via REST. Input chars: {len(text)}")

            # DashScope Qwen API via REST
            url = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "qwen-plus",  # qwen-flash doesn't follow dialogue formatting; qwen-plus is more capable
                "input": {
                    "messages": [
                        {"role": "user", "content": prompt}
                    ]
                },
                "parameters": {
                    "enable_thinking": False
                }
            }

            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                data = response.json()
                logging.info(f"Qwen response: {data}")

                # Extract text from response (prefer choices format —
                # it separates reasoning_content from content)
                formatted_text = ""
                if 'output' in data:
                    output = data['output']
                    if isinstance(output, dict):
                        if 'choices' in output:
                            choices = output['choices']
                            if choices and isinstance(choices, list):
                                formatted_text = choices[0].get('message', {}).get('content', '')
                        if not formatted_text and 'text' in output:
                            formatted_text = output['text']

                formatted_text = formatted_text.strip()

                # Strip <think> blocks leaked by Qwen3 hybrid-thinking models
                import re
                if '<think>' in formatted_text:
                    logging.warning("Qwen response contains <think> tags, stripping")
                    formatted_text = re.sub(r'<think>.*?</think>', '', formatted_text, flags=re.DOTALL).strip()

                api_duration = time.time() - api_start_time
                logging.info(f"Qwen LLM finished. Duration: {api_duration:.2f}s, Output chars: {len(formatted_text)}")

                # Remove code tags if present but not wanted
                if not use_code_tags and formatted_text.startswith('<code>'):
                    formatted_text = formatted_text.replace('<code>', '').replace('</code>', '')

                # Quality check
                if len(formatted_text) < 5:
                    if _is_fallback:
                        logging.warning("Qwen returned very short text, returning original")
                        return text
                    logging.warning("Qwen returned very short text, trying AssemblyAI fallback")
                    return self.format_text_with_assemblyai(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

                # Log API call metrics
                if self.metrics_service:
                    self.metrics_service.log_api_call('qwen-llm', api_duration, True)

                logging.info("Successfully formatted text with Qwen LLM")
                return formatted_text
            else:
                if _is_fallback:
                    logging.warning(f"Qwen API error: {response.status_code} - {response.text}, returning original text")
                    return text
                logging.warning(f"Qwen API error: {response.status_code} - {response.text}, trying AssemblyAI fallback")
                return self.format_text_with_assemblyai(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

        except requests.RequestException as e:
            if _is_fallback:
                logging.warning(f"Qwen API request failed: {e}, returning original text")
                return text
            logging.warning(f"Qwen API request failed: {e}, falling back to AssemblyAI")
            return self.format_text_with_assemblyai(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

        except Exception as e:
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('qwen-llm', api_duration, False, str(e))

            if _is_fallback:
                logging.warning(f"Qwen LLM failed: {e}, returning original text")
                return text
            logging.warning(f"Qwen LLM failed: {e}, falling back to AssemblyAI")
            return self.format_text_with_assemblyai(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

    def format_text_with_assemblyai(self, text: str, use_code_tags: bool = False,
                                      use_yo: bool = True, is_chunked: bool = False,
                                      is_dialogue: bool = False,
                                      _is_fallback: bool = False) -> str:
        """
        Format transcribed text using AssemblyAI LLM Gateway (Gemini 3 Flash).
        Falls back to Qwen if this fails (unless already a fallback call).
        """
        import requests

        # Check if text is too short to format
        word_count = len(text.split())
        if word_count < 10:
            logging.info(f"Text too short for LLM formatting ({word_count} words < 10), returning original")
            return text

        api_start_time = time.time()

        try:
            api_key = os.environ.get('ASSEMBLYAI_API_KEY')
            if not api_key:
                if _is_fallback:
                    logging.warning("ASSEMBLYAI_API_KEY not set, returning original text")
                    return text
                logging.warning("ASSEMBLYAI_API_KEY not set, falling back to Qwen")
                return self.format_text_with_qwen(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

            prompt = self._build_format_prompt(text, use_code_tags, use_yo, is_chunked, is_dialogue)

            logging.info(f"Starting AssemblyAI LLM request (Gemini 3 Flash). Input chars: {len(text)}")

            url = "https://llm-gateway.assemblyai.com/v1/chat/completions"

            headers = {
                'Authorization': api_key,
                'Content-Type': 'application/json'
            }

            payload = {
                'model': 'gemini-3-flash-preview',
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'max_tokens': 8192
            }

            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                data = response.json()
                logging.info(f"AssemblyAI LLM response keys: {list(data.keys())}")

                formatted_text = ""
                if 'choices' in data and data['choices']:
                    formatted_text = data['choices'][0].get('message', {}).get('content', '')

                formatted_text = formatted_text.strip()
                api_duration = time.time() - api_start_time
                logging.info(f"AssemblyAI LLM finished. Duration: {api_duration:.2f}s, Output chars: {len(formatted_text)}")

                # Remove code tags if present but not wanted
                if not use_code_tags and formatted_text.startswith('<code>'):
                    formatted_text = formatted_text.replace('<code>', '').replace('</code>', '')

                # Quality check
                if len(formatted_text) < 5:
                    if _is_fallback:
                        logging.warning("AssemblyAI LLM returned very short text, returning original")
                        return text
                    logging.warning("AssemblyAI LLM returned very short text, trying Qwen fallback")
                    return self.format_text_with_qwen(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

                # Log API call metrics
                if self.metrics_service:
                    self.metrics_service.log_api_call('assemblyai-llm', api_duration, True)

                logging.info("Successfully formatted text with AssemblyAI LLM")
                return formatted_text
            else:
                if _is_fallback:
                    logging.warning(f"AssemblyAI LLM API error: {response.status_code} - {response.text}, returning original text")
                    return text
                logging.warning(f"AssemblyAI LLM API error: {response.status_code} - {response.text}, trying Qwen fallback")
                return self.format_text_with_qwen(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

        except requests.RequestException as e:
            if _is_fallback:
                logging.warning(f"AssemblyAI LLM request failed: {e}, returning original text")
                return text
            logging.warning(f"AssemblyAI LLM request failed: {e}, falling back to Qwen")
            return self.format_text_with_qwen(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

        except Exception as e:
            api_duration = time.time() - api_start_time
            if self.metrics_service:
                self.metrics_service.log_api_call('assemblyai-llm', api_duration, False, str(e))

            if _is_fallback:
                logging.warning(f"AssemblyAI LLM failed: {e}, returning original text")
                return text
            logging.warning(f"AssemblyAI LLM failed: {e}, falling back to Qwen")
            return self.format_text_with_qwen(text, use_code_tags, use_yo, is_chunked, is_dialogue, _is_fallback=True)

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