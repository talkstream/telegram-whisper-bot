"""
Telegram Whisper Bot - Main Application
Refactored version: Lightweight Bot, Heavy Worker
"""

import os
import json
import logging
import math
import uuid
from datetime import datetime
import pytz

from flask import Flask
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
    """
    Process media file (audio, voice, video, video_note, or document)
    Publishes metadata to Pub/Sub for async processing.
    """
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
                'batch_files': [
                    {
                        'file_id': file_id,
                        'file_size': file_size,
                        'duration': duration,
                        'file_type': file_type
                    }
                ],
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
            
            # Check if we should process the batch (simple timeout logic)
            batch_start = user_state.get('batch_start_time', datetime.now(pytz.utc))
            if isinstance(batch_start, str):
                batch_start = datetime.fromisoformat(batch_start.replace('Z', '+00:00'))
            
            time_elapsed = (datetime.now(pytz.utc) - batch_start).total_seconds()
            if time_elapsed > 2:  # 2 second timeout for batch collection
                # Process the entire batch
                return process_batch_files(user_id, chat_id, user_name, user_data, user_state)
            
            return "OK", 200
    
    # Single file processing
    # Check balance for async processing
    balance = user_data.get('balance_minutes', 0)
    estimated_duration = duration / 60 if duration > 0 else 5.0 # Default 5 mins if unknown
    
    if balance < estimated_duration:
        telegram_service.send_message(chat_id,
            f"Недостаточно минут. Требуется ~{math.ceil(estimated_duration)} мин, "
            f"ваш баланс: {math.ceil(balance)} мин.")
        return "OK", 200
    
    # Send initial status message
    msg_text = "🎵 Аудио получено. Обрабатываю..."
    if file_type in ['video', 'video_note']:
        msg_text = "🎥 Видео получено. Обрабатываю..."
        
    status_msg = telegram_service.send_message(chat_id, msg_text)
    status_message_id = status_msg.get('result', {}).get('message_id') if status_msg else None
    
    # Publish to Pub/Sub for async processing
    job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, 
                             user_name, status_message_id)
    logging.info(f"Published job {job_id} for user {user_id} (type: {file_type})")
    
    return "OK", 200


def process_batch_files(user_id, chat_id, user_name, user_data, batch_state):
    """Process a batch of audio/video files"""
    batch_files = batch_state.get('batch_files', [])
    
    if not batch_files:
        return "OK", 200
    
    # Calculate total duration and check balance
    total_duration = sum(f.get('duration', 0) for f in batch_files) / 60
    balance = user_data.get('balance_minutes', 0)
    
    if balance < total_duration:
        telegram_service.send_message(chat_id,
            f"Недостаточно минут для обработки {len(batch_files)} файлов. "
            f"Требуется ~{math.ceil(total_duration)} мин, ваш баланс: {math.ceil(balance)} мин.")
        services.firestore_service.set_user_state(user_id, None)
        return "OK", 200
    
    # Send batch confirmation message  
    batch_msg = (
        f"📦 Получено {UtilityService.pluralize_russian(len(batch_files), 'файл', 'файла', 'файлов')}
"
        f"⏱ Общая длительность: ~{math.ceil(total_duration)} мин
"
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
        
        logging.info(f"Published batch job {job_id} ({idx+1}/{len(batch_files)}) for user {user_id}")
    
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
    
    # Publish to Pub/Sub (convert datetime to ISO format for JSON serialization)
    pubsub_data = job_data.copy()
    pubsub_data['created_at'] = job_data['created_at'].isoformat()
    
    topic_path = services.publisher.topic_path(services.PROJECT_ID, services.AUDIO_PROCESSING_TOPIC)
    message_data = json.dumps(pubsub_data).encode('utf-8')
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