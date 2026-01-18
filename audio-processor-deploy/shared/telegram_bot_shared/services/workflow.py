import logging
import json
import uuid
import math
import asyncio
from datetime import datetime
import pytz

from .utility import UtilityService

class WorkflowService:
    """
    Service for managing audio processing workflows and batch operations.
    Async version using Aiogram.
    """
    
    def __init__(self, firestore_service, telegram_service, publisher, project_id, audio_processing_topic, db, max_file_size):
        self.firestore_service = firestore_service
        self.telegram_service = telegram_service  # Expecting AsyncTelegramService
        self.publisher = publisher
        self.project_id = project_id
        self.audio_processing_topic = audio_processing_topic
        self.db = db
        self.max_file_size = max_file_size

    async def process_audio_file(self, file_info, user_id, chat_id, user_name, user_data, file_type):
        """
        Process media file (audio, voice, video, video_note, or document)
        Publishes metadata to Pub/Sub for async processing.
        """
        file_id = file_info['file_id']
        file_size = file_info.get('file_size', 0)
        duration = file_info.get('duration', 0)
        
        # Validate file size
        if file_size > self.max_file_size:
            size_mb = file_size / (1024 * 1024)
            max_mb = self.max_file_size / (1024 * 1024)
            await self.telegram_service.send_message(chat_id, 
                f"Файл слишком большой ({size_mb:.1f} MB). Максимальный размер: {max_mb} MB.")
            return "OK", 200
        
        # Handle batch processing
        user_state = self.firestore_service.get_user_state(user_id) if self.firestore_service else None
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
                self.firestore_service.set_user_state(user_id, batch_state)
                await self.telegram_service.send_message(chat_id, "📦 Получена группа файлов. Обрабатываю...")
                return "OK", 200
            else:
                # Additional file in the batch
                user_state['batch_files'].append({
                    'file_id': file_id,
                    'file_size': file_size,
                    'duration': duration,
                    'file_type': file_type
                })
                self.firestore_service.set_user_state(user_id, user_state)
                
                # Check if we should process the batch (simple timeout logic)
                batch_start = user_state.get('batch_start_time', datetime.now(pytz.utc))
                if isinstance(batch_start, str):
                    batch_start = datetime.fromisoformat(batch_start.replace('Z', '+00:00'))
                
                time_elapsed = (datetime.now(pytz.utc) - batch_start).total_seconds()
                if time_elapsed > 2:  # 2 second timeout for batch collection
                    # Process the entire batch
                    return await self.process_batch_files(user_id, chat_id, user_name, user_data, user_state)
                
                return "OK", 200
        
        # Single file processing
        # Check balance for async processing
        balance = user_data.get('balance_minutes', 0)
        estimated_duration = duration / 60 if duration > 0 else 5.0 # Default 5 mins if unknown
        
        if balance < estimated_duration:
            await self.telegram_service.send_message(chat_id,
                f"Недостаточно минут. Требуется ~{math.ceil(estimated_duration)} мин, "
                f"ваш баланс: {math.ceil(balance)} мин.")
            return "OK", 200
        
        # Send initial status message
        msg_text = "🎵 Аудио получено. Обрабатываю..."
        if file_type in ['video', 'video_note']:
            msg_text = "🎥 Видео получено. Обрабатываю..."
            
        status_msg = await self.telegram_service.send_message(chat_id, msg_text)
        status_message_id = status_msg.message_id if status_msg else None
        
        # Publish to Pub/Sub for async processing
        job_id = await self.publish_audio_job(user_id, chat_id, file_id, file_size, duration, 
                                 user_name, status_message_id)
        logging.info(f"Published job {job_id} for user {user_id} (type: {file_type})")
        
        return "OK", 200

    async def process_batch_files(self, user_id, chat_id, user_name, user_data, batch_state):
        """Process a batch of audio/video files"""
        batch_files = batch_state.get('batch_files', [])
        
        if not batch_files:
            return "OK", 200
        
        # Calculate total duration and check balance
        total_duration = sum(f.get('duration', 0) for f in batch_files) / 60
        balance = user_data.get('balance_minutes', 0)
        
        if balance < total_duration:
            await self.telegram_service.send_message(chat_id,
                f"Недостаточно минут для обработки {len(batch_files)} файлов. "
                f"Требуется ~{math.ceil(total_duration)} мин, ваш баланс: {math.ceil(balance)} мин.")
            self.firestore_service.set_user_state(user_id, None)
            return "OK", 200
        
        # Send batch confirmation message  
        batch_msg = (
            f"📦 Получено {UtilityService.pluralize_russian(len(batch_files), 'файл', 'файла', 'файлов')}\n"
            f"⏱ Общая длительность: ~{math.ceil(total_duration)} мин\n"
            f"⏳ Начинаю обработку..."
        )
        
        status_msg = await self.telegram_service.send_message(chat_id, batch_msg)
        status_message_id = status_msg.message_id if status_msg else None
        
        # Process each file in the batch
        for idx, file_data in enumerate(batch_files):
            file_id = file_data['file_id']
            file_size = file_data.get('file_size', 0)
            duration = file_data.get('duration', 0)
            
            # Publish job with batch confirmation flag
            is_last_file = (idx == len(batch_files) - 1)
            job_id = await self.publish_audio_job(user_id, chat_id, file_id, file_size, duration,
                                     user_name, status_message_id if is_last_file else None,
                                     is_batch_confirmation=is_last_file)
            
            logging.info(f"Published batch job {job_id} ({idx+1}/{len(batch_files)}) for user {user_id}")
        
        # Clear batch state
        self.firestore_service.set_user_state(user_id, None)
        
        return "OK", 200

    async def publish_audio_job(self, user_id, chat_id, file_id, file_size, duration, user_name, 
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
        
        # Save to Firestore (Sync call)
        self.db.collection('audio_jobs').document(job_id).set(job_data)
        
        # Publish to Pub/Sub (convert datetime to ISO format for JSON serialization)
        pubsub_data = job_data.copy()
        pubsub_data['created_at'] = job_data['created_at'].isoformat()
        
        topic_path = self.publisher.topic_path(self.project_id, self.audio_processing_topic)
        message_data = json.dumps(pubsub_data).encode('utf-8')
        
        def publish_sync():
            future = self.publisher.publish(topic_path, message_data)
            return future.result(timeout=30)
        
        # Log the result
        try:
            # Execute publish in thread pool to avoid blocking async loop
            message_id = await asyncio.to_thread(publish_sync)
            logging.info(f"Published message {message_id} for job {job_id}")
        except Exception as e:
            logging.error(f"Failed to publish job {job_id}: {e}")
            # Update job status to failed
            self.db.collection('audio_jobs').document(job_id).update({
                'status': 'failed',
                'error': str(e)
            })
            # Notify user
            await self.telegram_service.send_message(chat_id,
                "Произошла ошибка при отправке задачи на обработку. Попробуйте позже.")
        
        return job_id
