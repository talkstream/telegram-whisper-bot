"""
Telegram Whisper Bot - Main Application
Simplified and refactored version
"""

import os
import json
import logging
import math
import tempfile
import subprocess
import uuid
from datetime import datetime
import pytz

from flask import Flask
import requests
from google.cloud import pubsub_v1
from google.cloud.firestore_v1.base_query import FieldFilter

# Import app modules
from app.initialization import services
from app.routes import register_routes

# Import services for convenience
from services import telegram as telegram_service
from services.utility import UtilityService

# Configure logging
logging.basicConfig(level=logging.INFO)

# Create Flask app
app = Flask(__name__)

# Register all routes
register_routes(app, services)


# --- AUDIO/VIDEO PROCESSING FUNCTIONS ---

def process_audio_file(file_info, user_id, chat_id, user_name, user_data, file_type):
    """Process audio file (audio, voice, or document)"""
    file_id = file_info['file_id']
    file_size = file_info.get('file_size', 0)
    duration = file_info.get('duration', 0)
    
    # Validate file size
    if file_size > services.MAX_TELEGRAM_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        max_mb = services.MAX_TELEGRAM_FILE_SIZE / (1024 * 1024)
        telegram_service.send_message(chat_id, 
            f"Файл слишком большой ({size_mb:.1f} MB). Максимальный размер: {max_mb} MB.")
        return "OK", 200
    
    # Handle batch processing
    user_state = services.firestore_service.get_user_state(user_id) if services.firestore_service else None
    media_group_id = None
    
    # Check for media group (batch)
    if hasattr(file_info, 'get') and 'media_group_id' in file_info:
        media_group_id = file_info.get('media_group_id')
        
    if media_group_id:
        # This is part of a batch
        if not user_state or user_state.get('current_batch_id') != media_group_id:
            # First file in the batch
            batch_state = {
                'current_batch_id': media_group_id,
                'batch_files': [{
                    'file_id': file_id,
                    'file_size': file_size,
                    'duration': duration,
                    'file_type': file_type
                }],
                'batch_start_time': datetime.now(pytz.utc)
            }
            services.firestore_service.set_user_state(user_id, batch_state)
            telegram_service.send_message(chat_id, "📦 Получена группа файлов. Обрабатываю...")
            return "OK", 200
        else:
            # Additional file in the batch
            user_state['batch_files'].append({
                'file_id': file_id,
                'file_size': file_size,
                'duration': duration,
                'file_type': file_type
            })
            services.firestore_service.set_user_state(user_id, user_state)
            
            # Check if we should process the batch (simple timeout)
            batch_start = user_state.get('batch_start_time', datetime.now(pytz.utc))
            if isinstance(batch_start, str):
                batch_start = datetime.fromisoformat(batch_start.replace('Z', '+00:00'))
            
            time_elapsed = (datetime.now(pytz.utc) - batch_start).total_seconds()
            if time_elapsed > 2:  # 2 second timeout for batch collection
                # Process the entire batch
                return process_batch_files(user_id, chat_id, user_name, user_data, user_state)
            
            return "OK", 200
    
    # Single file processing
    if services.USE_ASYNC_PROCESSING:
        # Check balance for async processing
        balance = user_data.get('balance_minutes', 0)
        estimated_duration = duration / 60 if duration > 0 else 5.0
        
        if balance < estimated_duration:
            telegram_service.send_message(chat_id,
                f"Недостаточно минут. Требуется ~{estimated_duration:.1f} мин, "
                f"ваш баланс: {balance:.1f} мин.")
            return "OK", 200
        
        # Send initial status message
        status_msg = telegram_service.send_message(chat_id, "🎵 Аудио получено. Обрабатываю...")
        status_message_id = status_msg.get('result', {}).get('message_id') if status_msg else None
        
        # Publish to Pub/Sub for async processing
        job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, 
                                 user_name, status_message_id)
        logging.info(f"Published audio job {job_id} for user {user_id}")
    else:
        # Synchronous processing (not implemented in this simplified version)
        telegram_service.send_message(chat_id, 
            "Синхронная обработка временно недоступна. Попробуйте позже.")
    
    return "OK", 200


def process_video_file(file_info, user_id, chat_id, user_name, user_data):
    """Process video file by extracting audio"""
    file_id = file_info['file_id']
    file_size = file_info.get('file_size', 0)
    
    # Validate file size
    if file_size > services.MAX_TELEGRAM_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        max_mb = services.MAX_TELEGRAM_FILE_SIZE / (1024 * 1024)
        telegram_service.send_message(chat_id,
            f"Видео слишком большое ({size_mb:.1f} MB). Максимальный размер: {max_mb} MB.")
        return "OK", 200
    
    # Download video file
    telegram_service.send_message(chat_id, "🎬 Извлекаю аудиодорожку...")
    
    try:
        # Get file path from Telegram
        file_path_response = requests.get(
            f"{services.telegram_api_url}/getFile",
            params={'file_id': file_id}
        )
        file_path_data = file_path_response.json()
        
        if not file_path_data.get('ok'):
            raise Exception("Failed to get file path")
        
        file_path = file_path_data['result']['file_path']
        
        # Download the video file
        download_url = f"{services.telegram_file_url}/{file_path}"
        video_response = requests.get(download_url)
        
        if video_response.status_code != 200:
            raise Exception(f"Failed to download video: {video_response.status_code}")
        
        # Save video to temporary file
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as video_file:
            video_file.write(video_response.content)
            video_path = video_file.name
        
        # Extract audio using FFmpeg
        audio_path = video_path.replace('.mp4', '_audio.mp3')
        
        try:
            # Use FFmpeg to extract audio
            cmd = [
                'ffmpeg', '-i', video_path,
                '-vn',  # No video
                '-acodec', 'mp3',
                '-ab', '128k',
                '-ar', '44100',
                '-ac', '1',  # Mono
                '-y',  # Overwrite output
                audio_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                if "Stream map '0:a:0' matches no streams" in result.stderr:
                    telegram_service.send_message(chat_id,
                        "В этом видео нет аудиодорожки. Пожалуйста, отправьте видео со звуком.")
                    return "OK", 200
                else:
                    raise Exception(f"FFmpeg error: {result.stderr}")
            
            # Get audio duration
            duration_cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ]
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True)
            duration = float(duration_result.stdout.strip()) if duration_result.returncode == 0 else 0
            
            # Upload extracted audio to Telegram and get new file_id
            with open(audio_path, 'rb') as audio_file:
                # Send as voice message to get file_id
                response = requests.post(
                    f"{services.telegram_api_url}/sendVoice",
                    data={'chat_id': chat_id},
                    files={'voice': audio_file}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('ok'):
                        # Delete the message we just sent
                        message_id = result['result']['message_id']
                        requests.post(
                            f"{services.telegram_api_url}/deleteMessage",
                            json={'chat_id': chat_id, 'message_id': message_id}
                        )
                        
                        # Get the audio file_id
                        audio_file_id = result['result']['voice']['file_id']
                        audio_file_size = os.path.getsize(audio_path)
                        
                        # Process the extracted audio
                        audio_info = {
                            'file_id': audio_file_id,
                            'file_size': audio_file_size,
                            'duration': int(duration)
                        }
                        
                        # Update status message
                        telegram_service.send_message(chat_id, "🎵 Аудио извлечено. Обрабатываю...")
                        
                        return process_audio_file(audio_info, user_id, chat_id, user_name, 
                                                user_data, 'extracted_audio')
        
        finally:
            # Clean up temporary files
            if os.path.exists(video_path):
                os.remove(video_path)
            if os.path.exists(audio_path):
                os.remove(audio_path)
    
    except Exception as e:
        logging.error(f"Error processing video: {e}")
        telegram_service.send_message(chat_id,
            "Произошла ошибка при обработке видео. Попробуйте другой файл.")
    
    return "OK", 200


def process_batch_files(user_id, chat_id, user_name, user_data, batch_state):
    """Process a batch of audio files"""
    batch_files = batch_state.get('batch_files', [])
    
    if not batch_files:
        return "OK", 200
    
    # Calculate total duration and check balance
    total_duration = sum(f.get('duration', 0) for f in batch_files) / 60
    balance = user_data.get('balance_minutes', 0)
    
    if balance < total_duration:
        telegram_service.send_message(chat_id,
            f"Недостаточно минут для обработки {len(batch_files)} файлов. "
            f"Требуется ~{total_duration:.1f} мин, ваш баланс: {balance:.1f} мин.")
        services.firestore_service.set_user_state(user_id, None)
        return "OK", 200
    
    # Send batch confirmation message  
    batch_msg = (f"📦 Получено {UtilityService.pluralize_russian(len(batch_files), 'файл', 'файла', 'файлов')}\n"
                f"⏱ Общая длительность: ~{total_duration:.1f} мин\n"
                f"⏳ Начинаю обработку...")
    
    status_msg = telegram_service.send_message(chat_id, batch_msg)
    status_message_id = status_msg.get('result', {}).get('message_id') if status_msg else None
    
    # Process each file in the batch
    for idx, file_data in enumerate(batch_files):
        file_id = file_data['file_id']
        file_size = file_data.get('file_size', 0)
        duration = file_data.get('duration', 0)
        
        # Publish job with batch confirmation flag
        is_last_file = (idx == len(batch_files) - 1)
        job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration,
                                 user_name, status_message_id if is_last_file else None,
                                 is_batch_confirmation=is_last_file)
        
        logging.info(f"Published batch audio job {job_id} ({idx+1}/{len(batch_files)}) for user {user_id}")
    
    # Clear batch state
    services.firestore_service.set_user_state(user_id, None)
    
    return "OK", 200


def publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, 
                     status_message_id=None, is_batch_confirmation=False):
    """Publish audio processing job to Pub/Sub"""
    job_id = str(uuid.uuid4())
    
    # Create job document in Firestore
    job_data = {
        'job_id': job_id,
        'user_id': str(user_id),
        'chat_id': chat_id,
        'file_id': file_id,
        'file_size': file_size,
        'duration': duration,
        'user_name': user_name,
        'status': 'pending',
        'created_at': datetime.now(pytz.utc),
        'status_message_id': status_message_id,
        'is_batch_confirmation': is_batch_confirmation
    }
    
    # Save to Firestore
    services.db.collection('audio_jobs').document(job_id).set(job_data)
    
    # Publish to Pub/Sub
    topic_path = services.publisher.topic_path(services.PROJECT_ID, services.AUDIO_PROCESSING_TOPIC)
    message_data = json.dumps(job_data).encode('utf-8')
    future = services.publisher.publish(topic_path, message_data)
    
    # Log the result
    try:
        message_id = future.result(timeout=30)
        logging.info(f"Published message {message_id} for job {job_id}")
    except Exception as e:
        logging.error(f"Failed to publish job {job_id}: {e}")
        # Update job status to failed
        services.db.collection('audio_jobs').document(job_id).update({
            'status': 'failed',
            'error': str(e)
        })
        # Notify user
        telegram_service.send_message(chat_id,
            "Произошла ошибка при отправке задачи на обработку. Попробуйте позже.")
    
    return job_id


if __name__ == '__main__':
    # Initialize services on startup
    if not services.initialize():
        logging.error("Failed to initialize services")
        exit(1)
    
    # Run Flask app (for local testing only)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)