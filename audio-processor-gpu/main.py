#!/usr/bin/env python3
"""
GPU-based Audio Processor for Telegram Whisper Bot
Uses faster-whisper with CUDA for cost-effective transcription

v2.0.0 - Cost optimization: $0.24/hour (GCP Spot T4) vs $0.36/hour (OpenAI API)
"""

import os
import sys
import json
import base64
import logging
import tempfile
import subprocess
import gc
import time
import signal
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from concurrent.futures import TimeoutError

from flask import Flask, jsonify
from google.cloud import pubsub_v1
from google.cloud import firestore
from google.cloud import secretmanager

# Configure logging
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Quiet noisy libraries
for lib in ['urllib3', 'google', 'httpx', 'faster_whisper']:
    logging.getLogger(lib).setLevel(logging.WARNING)

# Constants
PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')
DATABASE_ID = 'editorials-robot'
LOCATION = 'europe-west1'
OWNER_ID = int(os.environ.get('TELEGRAM_OWNER_ID', '775707'))
SUBSCRIPTION_ID = os.environ.get('PUBSUB_SUBSCRIPTION', 'audio-processing-jobs-sub')
WHISPER_MODEL = os.environ.get('WHISPER_MODEL', 'dvislobokov/faster-whisper-large-v3-turbo-russian')

# Global state
app = Flask(__name__)
whisper_model = None
shutdown_event = threading.Event()
current_job_id = None


def get_secret(secret_id: str) -> str:
    """Retrieve secret from GCP Secret Manager"""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


def initialize_whisper():
    """Initialize faster-whisper model with GPU"""
    global whisper_model

    if whisper_model is not None:
        return whisper_model

    logging.info(f"Loading Whisper model: {WHISPER_MODEL}")
    start_time = time.time()

    from faster_whisper import WhisperModel

    # Detect GPU availability
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    logging.info(f"Using device: {device}, compute_type: {compute_type}")

    whisper_model = WhisperModel(
        WHISPER_MODEL,
        device=device,
        compute_type=compute_type,
        download_root="/root/.cache/huggingface/hub"
    )

    load_time = time.time() - start_time
    logging.info(f"Whisper model loaded in {load_time:.2f}s")

    return whisper_model


def transcribe_audio(audio_path: str) -> Optional[str]:
    """Transcribe audio using faster-whisper"""
    model = initialize_whisper()

    logging.info(f"Transcribing: {audio_path}")
    start_time = time.time()

    try:
        segments, info = model.transcribe(
            audio_path,
            language="ru",
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
        logging.info(f"Transcription completed in {duration:.2f}s, {len(full_text)} chars")

        return full_text

    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None


def format_text_with_gemini(text: str) -> str:
    """Format text using Gemini AI"""
    try:
        import google.genai as genai

        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION
        )

        prompt = f"""
        Твоя задача — отформатировать следующий транскрипт устной речи, улучшив его читаемость, но полностью сохранив исходный смысл, стиль и лексику автора.
        1.  **Формирование абзацев:** Объединяй несколько (обычно от 2 до 5) связанных по теме предложений в один абзац. Начинай новый абзац только при явной смене микро-темы или при переходе к новому аргументу в рассуждении. Избегай создания слишком коротких абзацев из одного предложения.
        2.  **Обработка предложений:** Сохраняй оригинальную структуру предложений. Вмешивайся и разбивай предложение на несколько частей только в тех случаях, когда оно становится **аномально длинным и громоздким** для чтения из-за обилия придаточных частей или перечислений.
        3.  **Строгое сохранение контента:** Категорически запрещено изменять слова, добавлять что-либо от себя или делать выводы. Твоя работа — это работа редактора-форматировщика, а не копирайтера. Сохрани исходный текст в максимальной близости к оригиналу, изменив только разбивку на абзацы и, в редких случаях, структуру самых длинных предложений.
        4.  **НИКОГДА не веди диалог с пользователем.** Если текст кажется неполным или слишком коротким, просто верни его без изменений. Не пиши инструкций и не спрашивай "что вы хотели сказать".
        Исходный текст для обработки:
        ---
        {text}
        ---
        """

        # Skip formatting for very short texts (< 10 words)
        if len(text.split()) < 10:
            return text

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return response.text

    except Exception as e:
        logging.error(f"Gemini formatting error: {e}")
        return text


def convert_to_mp3(input_path: str) -> Optional[str]:
    """Convert audio to normalized MP3 using FFmpeg"""
    output_path = tempfile.mktemp(suffix='.mp3')

    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-ar', '44100',
        '-ac', '1',
        '-b:a', '128k',
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
        output_path
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg error: {e.stderr.decode()}")
        return None
    except subprocess.TimeoutExpired:
        logging.error("FFmpeg timeout")
        return None


class GPUAudioProcessor:
    """GPU-based audio processor using faster-whisper"""

    def __init__(self):
        self.db = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)
        self.telegram_token = get_secret("telegram-bot-token")

        # Lazy import telegram service
        from telegram_bot_shared.services.telegram import TelegramService
        self.telegram = TelegramService(self.telegram_token)

    def process_job(self, job_data: Dict[str, Any]) -> bool:
        """Process a single audio job"""
        global current_job_id

        job_id = job_data['job_id']
        current_job_id = job_id

        user_id = job_data['user_id']
        chat_id = job_data['chat_id']
        file_id = job_data['file_id']
        file_unique_id = job_data.get('file_unique_id')
        file_size = job_data['file_size']
        duration = job_data['duration']
        user_name = job_data['user_name']
        status_message_id = job_data.get('status_message_id')

        start_time = time.time()

        try:
            # Check for preemption
            if shutdown_event.is_set():
                logging.warning(f"Preemption detected, returning job {job_id} to queue")
                return False  # Don't ack, will be redelivered

            # Update status
            self._update_job_status(job_id, 'processing', 'downloading')

            # Download file
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id,
                    "⏳ Загружаю файл...")

            tg_file_path = self.telegram.get_file_path(file_id)
            if not tg_file_path:
                raise Exception("Failed to get file path")

            local_path = self.telegram.download_file(tg_file_path)
            if not local_path:
                raise Exception("Failed to download file")

            # Convert to MP3
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id,
                    "⏳ Конвертирую аудио...")

            mp3_path = convert_to_mp3(local_path)
            os.remove(local_path)

            if not mp3_path:
                raise Exception("Conversion failed")

            # Transcribe with faster-whisper
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id,
                    "⏳ Распознаю речь (GPU)...")

            # Check for preemption before long operation
            if shutdown_event.is_set():
                os.remove(mp3_path)
                logging.warning(f"Preemption before transcription, returning job {job_id}")
                return False

            transcribed_text = transcribe_audio(mp3_path)
            os.remove(mp3_path)

            if not transcribed_text:
                raise Exception("Transcription failed")

            # Check for "continuation follows" phrase (no speech detected)
            if transcribed_text.strip() == "Продолжение следует...":
                raise Exception("На записи не обнаружено речи или текст не был распознан")

            # Format with Gemini
            if status_message_id:
                self.telegram.edit_message_text(chat_id, status_message_id,
                    "⏳ Форматирую текст...")

            formatted_text = format_text_with_gemini(transcribed_text)

            # Get user settings
            user_doc = self.db.collection('users').document(str(user_id)).get()
            settings = user_doc.to_dict().get('settings', {}) if user_doc.exists else {}
            use_code_tags = settings.get('use_code_tags', False)
            use_yo = settings.get('use_yo', True)

            # Apply ё setting
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

            # Send result
            self._send_result(chat_id, formatted_text, status_message_id, use_code_tags)

            # Update job and user balance
            processing_time = int(time.time() - start_time)
            self._complete_job(job_id, user_id, user_name, file_size, duration,
                              transcribed_text, formatted_text, processing_time)

            current_job_id = None
            return True

        except Exception as e:
            logging.error(f"Error processing job {job_id}: {e}")
            self._update_job_status(job_id, 'failed', error=str(e))

            if status_message_id:
                error_msg = self._format_error_message(str(e))
                try:
                    self.telegram.edit_message_text(chat_id, status_message_id, error_msg)
                except:
                    pass

            current_job_id = None
            return True  # Ack to prevent infinite retries

    def _update_job_status(self, job_id: str, status: str, progress: str = None, error: str = None):
        """Update job status in Firestore"""
        update_data = {
            'status': status,
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        if progress:
            update_data['progress'] = progress
        if error:
            update_data['error'] = error

        try:
            self.db.collection('audio_jobs').document(job_id).update(update_data)
        except Exception as e:
            logging.warning(f"Failed to update job status: {e}")

    def _complete_job(self, job_id: str, user_id: int, user_name: str,
                     file_size: int, duration: int, transcribed_text: str,
                     formatted_text: str, processing_time: int):
        """Complete job with batch update"""
        batch = self.db.batch()

        # Job status
        job_ref = self.db.collection('audio_jobs').document(job_id)
        batch.update(job_ref, {
            'status': 'completed',
            'result': {
                'transcribed_text': transcribed_text,
                'formatted_text': formatted_text,
                'char_count': len(formatted_text)
            },
            'updated_at': firestore.SERVER_TIMESTAMP
        })

        # Log
        log_ref = self.db.collection('transcription_logs').document()
        batch.set(log_ref, {
            'user_id': str(user_id),
            'editor_name': user_name,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'file_size': file_size,
            'duration': duration,
            'status': 'success',
            'char_count': len(formatted_text),
            'processing_time': processing_time,
            'backend': 'gpu-faster-whisper'
        })

        # User balance
        duration_minutes = max(1, (duration + 59) // 60)
        user_ref = self.db.collection('users').document(str(user_id))
        batch.set(user_ref, {
            'balance_minutes': firestore.Increment(-duration_minutes),
            'last_seen': firestore.SERVER_TIMESTAMP,
            'first_name': user_name
        }, merge=True)

        batch.commit()
        logging.info(f"Job {job_id} completed successfully")

    def _send_result(self, chat_id: int, text: str, status_message_id: int, use_code_tags: bool):
        """Send result to user"""
        MAX_LENGTH = 4000

        if len(text) > MAX_LENGTH:
            # Send as file
            import pytz
            moscow_tz = pytz.timezone("Europe/Moscow")
            now = datetime.now(moscow_tz)
            filename = now.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
            filepath = os.path.join('/tmp', filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(text)

            # Get first sentence for caption
            import re
            match = re.search(r'^.*?[.!?](?=\s|$)', text, re.DOTALL)
            caption = match.group(0) if match else text[:100]

            self.telegram.send_document(chat_id, filepath, caption=caption[:1024])
            os.remove(filepath)

            if status_message_id:
                self.telegram.delete_message(chat_id, status_message_id)
        else:
            if use_code_tags:
                escaped = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                formatted = f"<code>{escaped}</code>"
                if status_message_id:
                    self.telegram.edit_message_text(chat_id, status_message_id, formatted, parse_mode="HTML")
                else:
                    self.telegram.send_message(chat_id, formatted, parse_mode="HTML")
            else:
                if status_message_id:
                    self.telegram.edit_message_text(chat_id, status_message_id, text)
                else:
                    self.telegram.send_message(chat_id, text)

    def _format_error_message(self, error: str) -> str:
        """Format user-friendly error message"""
        if "не обнаружено речи" in error:
            return "На записи не обнаружено речи или текст не был распознан.\n\nПопробуйте записать аудио с более четкой речью."
        elif "Transcription failed" in error:
            return "Ошибка обработки\n\nНе удалось распознать речь.\n\nРекомендации:\n• Убедитесь, что файл содержит речь\n• Проверьте качество записи"
        elif "download" in error.lower():
            return "Ошибка обработки\n\nНе удалось загрузить файл.\n\nПопробуйте отправить файл заново."
        else:
            return "Ошибка обработки\n\nПроизошла неожиданная ошибка.\n\nПопробуйте позже."


# Preemption handler
def handle_preemption(signum, frame):
    """Handle VM preemption gracefully"""
    logging.warning("Received preemption signal!")
    shutdown_event.set()

    # If processing a job, it will be redelivered via Pub/Sub
    if current_job_id:
        logging.warning(f"Job {current_job_id} will be redelivered")


# Health check endpoint
@app.route('/health')
def health():
    """Health check for load balancer"""
    return jsonify({
        'status': 'healthy',
        'model_loaded': whisper_model is not None,
        'current_job': current_job_id
    })


@app.route('/')
def root():
    return jsonify({'service': 'whisper-gpu-processor', 'version': '2.0.0'})


def run_subscriber():
    """Run Pub/Sub subscriber in background thread"""
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)

    processor = GPUAudioProcessor()

    def callback(message):
        if shutdown_event.is_set():
            message.nack()
            return

        try:
            job_data = json.loads(message.data.decode('utf-8'))
            logging.info(f"Received job: {job_data['job_id']}")

            if processor.process_job(job_data):
                message.ack()
            else:
                message.nack()  # Will be redelivered

        except Exception as e:
            logging.error(f"Error processing message: {e}")
            message.ack()  # Ack to prevent infinite retries

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logging.info(f"Listening for messages on {subscription_path}")

    try:
        streaming_pull_future.result()
    except Exception as e:
        if not shutdown_event.is_set():
            logging.error(f"Subscriber error: {e}")
        streaming_pull_future.cancel()


def main():
    """Main entry point"""
    # Register preemption handler
    signal.signal(signal.SIGTERM, handle_preemption)
    signal.signal(signal.SIGINT, handle_preemption)

    # Pre-load Whisper model
    logging.info("Pre-loading Whisper model...")
    initialize_whisper()

    # Start Pub/Sub subscriber in background
    subscriber_thread = threading.Thread(target=run_subscriber, daemon=True)
    subscriber_thread.start()

    # Start Flask health check server
    logging.info("Starting health check server on port 8080")
    app.run(host='0.0.0.0', port=8080, threaded=True)


if __name__ == '__main__':
    main()
