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

    Supports both sync and async processing:
    - Sync: For short audio (<30 sec) - faster response
    - Async: For longer audio - via Pub/Sub
    """

    # Threshold for sync processing (seconds)
    SYNC_PROCESSING_THRESHOLD = 30

    def __init__(self, firestore_service, telegram_service, publisher, project_id,
                 audio_processing_topic, db, max_file_size, audio_service=None):
        self.firestore_service = firestore_service
        self.telegram_service = telegram_service  # Expecting AsyncTelegramService
        self.publisher = publisher
        self.project_id = project_id
        self.audio_processing_topic = audio_processing_topic
        self.db = db
        self.max_file_size = max_file_size
        self.audio_service = audio_service  # For sync processing

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
        
        # Handle batch processing (Async-safe Firestore)
        user_state = await asyncio.to_thread(self.firestore_service.get_user_state, user_id) if self.firestore_service else None
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
                await asyncio.to_thread(self.firestore_service.set_user_state, user_id, batch_state)
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
                await asyncio.to_thread(self.firestore_service.set_user_state, user_id, user_state)
                
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

        # Choose between sync and async processing
        # Sync is faster for short audio (<30 sec) - avoids Pub/Sub latency
        use_sync = (
            self.audio_service is not None and
            duration > 0 and
            duration <= self.SYNC_PROCESSING_THRESHOLD and
            file_type in ['audio', 'voice']  # Only for audio, not video
        )

        if use_sync:
            logging.info(f"Using SYNC processing for short audio ({duration}s) user {user_id}")
            return await self.process_audio_sync(
                file_id, file_size, duration, user_id, chat_id,
                user_name, user_data, status_message_id
            )

        # Publish to Pub/Sub for async processing
        job_id = await self.publish_audio_job(user_id, chat_id, file_id, file_size, duration,
                                 user_name, status_message_id)
        logging.info(f"Published job {job_id} for user {user_id} (type: {file_type})")

        return "OK", 200

    async def process_audio_sync(self, file_id, file_size, duration, user_id, chat_id,
                                  user_name, user_data, status_message_id):
        """
        Process audio synchronously (for short audio <30 sec).
        Faster than async because it avoids Pub/Sub latency.
        """
        import tempfile
        import os
        from google.cloud import firestore as fs

        try:
            # Update status
            await self.telegram_service.edit_message_text(
                chat_id, status_message_id, "⏳ Распознаю речь..."
            )

            # Download file from Telegram
            tg_file_path = await self.telegram_service.get_file_path(file_id)
            if not tg_file_path:
                raise Exception("Failed to get file path from Telegram")

            # Create temp file for download
            local_audio_path = os.path.join(tempfile.gettempdir(), f"audio_{file_id}.oga")
            downloaded_path = await self.telegram_service.download_file(tg_file_path, local_audio_path)
            if not downloaded_path:
                raise Exception("Failed to download file from Telegram")

            # Convert to MP3
            converted_path = await asyncio.to_thread(
                self.audio_service.convert_to_mp3, downloaded_path
            )

            # Clean up original file
            if os.path.exists(downloaded_path):
                os.remove(downloaded_path)

            if not converted_path:
                raise Exception("Conversion failed")

            # Get actual duration
            audio_info = await asyncio.to_thread(
                self.audio_service.get_audio_info, converted_path
            )
            actual_duration = audio_info.get('duration', duration) if audio_info else duration

            # Transcribe
            transcribed_text = await asyncio.to_thread(
                self.audio_service.transcribe_audio, converted_path
            )

            # Clean up converted file
            if converted_path and os.path.exists(converted_path):
                os.remove(converted_path)

            if not transcribed_text:
                raise Exception("Failed to transcribe audio")

            # Check for "Продолжение следует..." (no speech detected)
            if transcribed_text.strip() == "Продолжение следует...":
                await self.telegram_service.edit_message_text(
                    chat_id, status_message_id,
                    "На записи не обнаружено речи или текст не был распознан."
                )
                return "OK", 200

            # Get user settings
            settings = await asyncio.to_thread(
                self.firestore_service.get_user_settings, user_id
            )
            use_code_tags = settings.get('use_code_tags', False) if settings else False
            use_yo = settings.get('use_yo', True) if settings else True

            # Format text
            formatted_text = await asyncio.to_thread(
                self.audio_service.format_text_with_gemini, transcribed_text, use_code_tags, use_yo
            )

            # Replace ё with е if use_yo is False
            if not use_yo:
                formatted_text = formatted_text.replace('ё', 'е').replace('Ё', 'Е')

            # Send result
            MAX_MESSAGE_LENGTH = 4000
            if len(formatted_text) > MAX_MESSAGE_LENGTH:
                # Send as file
                moscow_tz = pytz.timezone("Europe/Moscow")
                now_moscow = datetime.now(moscow_tz)
                file_name = now_moscow.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
                temp_txt_path = os.path.join(tempfile.gettempdir(), file_name)

                with open(temp_txt_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_text)

                # Get first sentence for caption
                import re
                match = re.search(r'^.*?[.!?](?=\s|$)', formatted_text, re.DOTALL)
                caption = match.group(0) if match else formatted_text.split('\n')[0]
                if len(caption) > 1024:
                    caption = caption[:1021] + "..."

                await self.telegram_service.send_document(chat_id, temp_txt_path, caption=caption)
                os.remove(temp_txt_path)

                # Delete status message
                await self.telegram_service.delete_message(chat_id, status_message_id)
            else:
                # Send as message
                if use_code_tags:
                    escaped_text = formatted_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    await self.telegram_service.edit_message_text(
                        chat_id, status_message_id, f"<code>{escaped_text}</code>", parse_mode="HTML"
                    )
                else:
                    await self.telegram_service.edit_message_text(
                        chat_id, status_message_id, formatted_text
                    )

            # Update user balance
            billing_duration = int(actual_duration) if actual_duration else duration
            duration_minutes = max(1, (billing_duration + 59) // 60)
            await asyncio.to_thread(
                self.firestore_service.update_user_balance, user_id, -duration_minutes
            )

            # Log transcription
            log_data = {
                'user_id': str(user_id),
                'editor_name': user_name,
                'timestamp': fs.SERVER_TIMESTAMP,
                'file_size': file_size,
                'duration': duration,
                'status': 'success',
                'char_count': len(formatted_text),
                'sync_processing': True
            }
            await asyncio.to_thread(
                self.db.collection('transcription_logs').document().set, log_data
            )

            logging.info(f"SYNC processing completed for user {user_id}")
            return "OK", 200

        except Exception as e:
            logging.error(f"SYNC processing error: {e}")
            try:
                await self.telegram_service.edit_message_text(
                    chat_id, status_message_id,
                    "Ошибка обработки. Попробуйте позже."
                )
            except:
                pass
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
            await asyncio.to_thread(self.firestore_service.set_user_state, user_id, None)
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
        await asyncio.to_thread(self.firestore_service.set_user_state, user_id, None)
        
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
        
        # Save to Firestore (Sync call wrapped in thread)
        await asyncio.to_thread(self.db.collection('audio_jobs').document(job_id).set, job_data)
        
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
            await asyncio.to_thread(self.db.collection('audio_jobs').document(job_id).update, {
                'status': 'failed',
                'error': str(e)
            })
            # Notify user
            await self.telegram_service.send_message(chat_id,
                "Произошла ошибка при отправке задачи на обработку. Попробуйте позже.")
        
        return job_id
