"""
Flask routes for Telegram Whisper Bot
"""

import os
import json
import logging
import math
import tempfile
import subprocess
import uuid
from datetime import datetime, timedelta
import pytz

from flask import request, jsonify
from google.cloud.firestore_v1.base_query import FieldFilter
import requests

from services import telegram as telegram_service
from services.utility import UtilityService
from handlers.admin_commands import ReportCommandHandler


def register_routes(app, services):
    """Register all Flask routes with the app"""
    
    @app.route('/_ah/warmup')
    def warmup():
        """App Engine warmup handler"""
        elapsed = services.warmup()
        return f"Warmup completed in {elapsed:.2f} seconds", 200
    
    @app.route('/health')
    @app.route('/_ah/health')
    def health():
        """Health check endpoint"""
        return "OK", 200
    
    @app.route('/', methods=['POST'])
    def webhook():
        """Main webhook handler for Telegram updates"""
        if not services.initialized:
            if not services.initialize():
                return "Service initialization failed", 500
        
        try:
            update = request.get_json()
            if not update:
                return "OK", 200
            
            logging.info(f"Received update: {json.dumps(update)}")
            
            # Handle different update types
            if 'message' in update:
                message = update['message']
                if 'successful_payment' in message:
                    return handle_successful_payment(message, services)
                else:
                    return handle_message(message, services)
            elif 'pre_checkout_query' in update:
                return handle_pre_checkout_query(update['pre_checkout_query'], services)
            elif 'callback_query' in update:
                return handle_callback_query(update['callback_query'], services)
            
            return "OK", 200
            
        except Exception as e:
            logging.error(f"Error processing webhook: {e}", exc_info=True)
            return "Internal error", 500
    
    @app.route('/cleanup_stuck_jobs')
    def cleanup_stuck_jobs():
        """Clean up stuck audio processing jobs"""
        if not services.initialized:
            services.initialize()
            
        return cleanup_stuck_audio_jobs(services)
    
    @app.route('/send_payment_notifications')
    def send_payment_notifications():
        """Force send pending payment notifications"""
        if not services.initialized:
            services.initialize()
            
        services.notification_service._send_batched_payment_notifications()
        return "Payment notifications sent", 200
    
    @app.route('/send_trial_notifications')
    def send_trial_notifications():
        """Force check and send trial notifications"""
        if not services.initialized:
            services.initialize()
            
        services.notification_service.check_and_notify_trial_requests(force_check=True)
        return "Trial notifications checked", 200
    
    @app.route('/send_scheduled_report')
    def send_scheduled_report():
        """Send scheduled report (called by Cloud Scheduler)"""
        if not services.initialized:
            services.initialize()
            
        # Get report type from query parameter
        report_type = request.args.get('type', 'daily')
        
        # Create a mock update_data for the report handler
        update_data = {
            'user_id': services.OWNER_ID,
            'chat_id': services.OWNER_ID,
            'text': f'/report {report_type}',
            'user_data': {'balance_minutes': 999999}  # Admin always has balance
        }
        
        # Use the existing ReportCommandHandler
        services_dict = services._create_services_dict()
        constants_dict = services._create_constants_dict()
        report_handler = ReportCommandHandler(services_dict, constants_dict)
        result = report_handler.handle(update_data)
        
        return f"Scheduled {report_type} report sent", 200


def handle_message(message, services):
    """Handle incoming message"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    user_name = message['from'].get('first_name', f'User_{user_id}')
    
    # Get or create user
    user_data = services.firestore_service.get_user(user_id)
    if not user_data and 'text' in message and message['text'] == '/start':
        user_data = create_new_user(user_id, user_name, message['from'], services)
    
    # Check for stuck job cleanup
    check_and_cleanup_stuck_jobs(services)
    
    # Route to appropriate handler
    if 'text' in message:
        text = message['text']
        logging.info(f"Received command '{text}' from user {user_id} ({user_name})")
        
        # Special handling for /start command
        if text == '/start':
            return handle_start_command(user_id, chat_id, user_name, user_data, services)
        
        # Route to command handler
        update_data = {
            'text': text,
            'user_id': user_id,
            'chat_id': chat_id,
            'user_name': user_name,
            'user_data': user_data,
            'message': message
        }
        
        result = services.command_router.route(update_data)
        if result:
            return result
        
        # If not a command and user exists, might be audio
        if user_data and not text.startswith('/'):
            telegram_service.send_message(chat_id, 
                f"Пожалуйста, отправьте аудиофайл. Ваш баланс: {math.floor(user_data.get('balance_minutes', 0))} мин.")
    
    # Handle audio/video files
    elif any(key in message for key in ['audio', 'voice', 'video', 'video_note', 'document']):
        if not user_data:
            telegram_service.send_message(chat_id, 
                "Вы еще не зарегистрированы. Пожалуйста, отправьте /start для начала работы.")
            return "OK", 200
        
        return handle_media_message(message, user_id, chat_id, user_name, user_data, services)
    
    return "OK", 200


def handle_media_message(message, user_id, chat_id, user_name, user_data, services):
    """Handle audio, voice, video, and document messages"""
    # Import here to avoid circular imports
    from main import process_audio_file, process_video_file
    
    # Check balance
    balance = user_data.get('balance_minutes', 0)
    if balance < 0.5:
        telegram_service.send_message(chat_id, 
            "❌ Недостаточно минут на балансе. Используйте /buy_minutes для пополнения.")
        return "OK", 200
    
    # Determine file type and process
    if 'audio' in message:
        file_info = message['audio']
        return process_audio_file(file_info, user_id, chat_id, user_name, user_data, 'audio')
    elif 'voice' in message:
        file_info = message['voice'] 
        return process_audio_file(file_info, user_id, chat_id, user_name, user_data, 'voice')
    elif 'video' in message:
        file_info = message['video']
        telegram_service.send_message(chat_id, "🎥 Видео получено. Обрабатываю...")
        return process_video_file(file_info, user_id, chat_id, user_name, user_data)
    elif 'video_note' in message:
        file_info = message['video_note']
        telegram_service.send_message(chat_id, "🎥 Видео получено. Обрабатываю...")
        return process_video_file(file_info, user_id, chat_id, user_name, user_data)
    elif 'document' in message:
        file_info = message['document']
        mime_type = file_info.get('mime_type', '')
        
        # Check if document is audio
        if mime_type.startswith('audio/') or mime_type == 'application/ogg':
            return process_audio_file(file_info, user_id, chat_id, user_name, user_data, 'document')
        # Check if document is video
        elif mime_type.startswith('video/'):
            telegram_service.send_message(chat_id, "🎥 Видео получено. Обрабатываю...")
            return process_video_file(file_info, user_id, chat_id, user_name, user_data)
        else:
            telegram_service.send_message(chat_id, 
                "❌ Неподдерживаемый формат файла. Отправьте аудио или видео файл.")
    
    return "OK", 200


def handle_pre_checkout_query(query, services):
    """Handle Telegram Stars pre-checkout query"""
    query_id = query['id']
    payload = query['invoice_payload']
    
    # Validate the purchase
    if payload in services.PRODUCT_PACKAGES:
        telegram_service._telegram_service.answer_pre_checkout_query(query_id, True)
    else:
        telegram_service._telegram_service.answer_pre_checkout_query(query_id, False,
            error_message="Invalid product")
    
    return "OK", 200


def handle_successful_payment(message, services):
    """Handle successful Telegram Stars payment"""
    user_id = message['from']['id']
    chat_id = message['chat']['id']
    user_name = message['from'].get('first_name', f'User_{user_id}')
    payment = message['successful_payment']
    
    # Extract payment details
    stars_amount = payment['total_amount']
    payload = payment['invoice_payload']
    
    # Get package details
    package = services.PRODUCT_PACKAGES.get(payload)
    if not package:
        logging.error(f"Unknown payment payload: {payload}")
        return "OK", 200
    
    minutes_to_add = package['minutes']
    package_name = package['title']
    
    # Check if this is a micro package
    is_micro = payload == "buy_micro_10"
    
    # Update user balance
    if is_micro:
        # For micro package, also update purchase count
        user_data = services.firestore_service.get_user(user_id)
        current_count = user_data.get('micro_package_purchases', 0) if user_data else 0
        services.firestore_service.increment_micro_package_purchases(user_id, current_count)
    
    updated_user = services.firestore_service.update_user_balance(user_id, minutes_to_add)
    new_balance = updated_user.get('balance_minutes', 0) if updated_user else 0
    
    # Log the payment
    services.firestore_service.log_payment({
        'user_id': str(user_id),
        'user_name': user_name,
        'stars_amount': stars_amount,
        'minutes_credited': minutes_to_add,
        'package_name': package_name,
        'new_balance': new_balance,
        'timestamp': datetime.now(pytz.utc),
        'payment_type': 'telegram_stars'
    })
    
    # Send confirmation to user
    confirm_msg = f"✅ Оплата успешно получена!\n\n"
    confirm_msg += f"📦 {package_name}\n"
    confirm_msg += f"💫 Списано: {stars_amount} ⭐\n"
    confirm_msg += f"⏱ Начислено: {int(minutes_to_add)} минут\n"
    confirm_msg += f"💰 Ваш баланс: {math.ceil(new_balance)} минут"
    
    telegram_service.send_message(chat_id, confirm_msg)
    
    # Queue notification for owner
    services.notification_service.queue_payment_notification(
        user_id, user_name, stars_amount, minutes_to_add, package_name
    )
    
    return "OK", 200


def handle_callback_query(callback_query, services):
    """Handle inline keyboard callbacks"""
    callback_data = callback_query.get('data', '')
    user_id = callback_query['from']['id']
    
    if user_id != services.OWNER_ID:
        return "OK", 200
    
    # Handle trial request callbacks
    if callback_data.startswith('approve_trial_') or callback_data.startswith('deny_trial_'):
        # Import here to avoid circular imports
        from handlers.admin_commands import ReviewTrialsCommandHandler
        
        # Parse the callback
        parts = callback_data.split('_')
        action = parts[0]
        target_user_id = int(parts[2])
        
        # Process the action
        if action == 'approve':
            handle_trial_approval(target_user_id, callback_query, services)
        else:
            handle_trial_denial(target_user_id, callback_query, services)
    
    return "OK", 200


def handle_start_command(user_id, chat_id, user_name, user_data, services):
    """Handle /start command"""
    if user_data:
        balance = user_data.get('balance_minutes', 0)
        welcome_back_msg = f"С возвращением, {user_name}! Ваш текущий баланс: {math.floor(balance)} минут."
        telegram_service.send_message(chat_id, welcome_back_msg)
    else:
        welcome_msg = (f"Добро пожаловать, {user_name}! Я помогу транскрибировать ваши аудио в текст.\n\n"
                      "Для начала работы вам нужны минуты на балансе.\n"
                      "Используйте /trial для получения 15 бесплатных минут или /buy_minutes для покупки.")
        telegram_service.send_message(chat_id, welcome_msg)
    
    # Notify owner about trial requests if needed
    if user_id == services.OWNER_ID:
        services.notification_service.check_and_notify_trial_requests()
    
    return "OK", 200


def create_new_user(user_id, user_name, from_data, services):
    """Create a new user in the database"""
    user_data = {
        'first_name': user_name,
        'balance_minutes': 0,
        'added_at': datetime.now(pytz.utc),
        'trial_status': 'none',
        'settings': {'use_code_tags': False, 'use_yo': True}
    }
    
    # Add additional fields if available
    if 'last_name' in from_data:
        user_data['last_name'] = from_data['last_name']
    if 'username' in from_data:
        user_data['username'] = from_data['username']
    
    services.firestore_service.db.collection('users').document(str(user_id)).set(user_data)
    logging.info(f"Created new user: {user_id} ({user_name})")
    
    return user_data


def handle_trial_approval(target_user_id, callback_query, services):
    """Handle trial request approval"""
    # Update trial status
    services.firestore_service.update_user_trial_status(target_user_id, 'approved')
    
    # Add trial minutes
    services.firestore_service.credit_user(target_user_id, services.TRIAL_MINUTES)
    
    # Update the request
    services.firestore_service.db.collection('trial_requests').document(str(target_user_id)).update({
        'status': 'approved',
         'processed_at': datetime.now(pytz.utc),
        'processed_by': callback_query['from']['id']
    })
    
    # Notify the user
    telegram_service.send_message(target_user_id,
        f"✅ Ваша заявка на пробный доступ одобрена! На ваш баланс начислено {int(services.TRIAL_MINUTES)} минут.")
    
    # Update the admin message
    telegram_service._telegram_service.edit_message_text(
        callback_query['message']['chat']['id'],
        callback_query['message']['message_id'],
        f"✅ Заявка пользователя {target_user_id} одобрена и {int(services.TRIAL_MINUTES)} минут начислено."
    )
    
    # Delete the trial request message after a delay
    # Note: In production, this would be handled by a scheduled task
    logging.info(f"Trial request for user {target_user_id} approved")


def handle_trial_denial(target_user_id, callback_query, services):
    """Handle trial request denial"""
    # Update the request
    services.firestore_service.db.collection('trial_requests').document(str(target_user_id)).update({
        'status': 'denied',
        'processed_at': datetime.now(pytz.utc),
        'processed_by': callback_query['from']['id']
    })
    
    # Update the admin message
    telegram_service._telegram_service.edit_message_text(
        callback_query['message']['chat']['id'],
        callback_query['message']['message_id'],
        f"❌ Заявка пользователя {target_user_id} отклонена."
    )
    
    logging.info(f"Trial request for user {target_user_id} denied")


def check_and_cleanup_stuck_jobs(services):
    """Check and cleanup stuck jobs periodically"""
    # Only run cleanup every 30 minutes
    last_check_key = 'last_stuck_job_check'
    now = datetime.now(pytz.utc)
    
    # Get last check time from in-memory cache or database
    last_check = getattr(services, last_check_key, None)
    if last_check and (now - last_check).total_seconds() < 1800:  # 30 minutes
        return
    
    # Update last check time
    setattr(services, last_check_key, now)
    
    # Run cleanup
    cleanup_stuck_audio_jobs(services)


def cleanup_stuck_audio_jobs(services):
    """Clean up audio jobs stuck in pending/processing state"""
    one_hour_ago = datetime.now(pytz.utc) - timedelta(hours=1)
    
    stuck_jobs = services.db.collection('audio_jobs').where(
        filter=FieldFilter('status', 'in', ['pending', 'processing'])
    ).where(
        filter=FieldFilter('created_at', '<', one_hour_ago)
    ).stream()
    
    cleaned_count = 0
    for job_doc in stuck_jobs:
        job_id = job_doc.id
        job_data = job_doc.to_dict()
        
        # Update job as failed
        services.db.collection('audio_jobs').document(job_id).update({
            'status': 'failed',
            'error': 'Job timed out after 1 hour',
            'updated_at': datetime.now(pytz.utc)
        })
        
        # Refund user if they were charged
        user_id = job_data.get('user_id')
        duration = job_data.get('duration', 0)
        if user_id and duration > 0:
            refund_amount = duration / 60
            services.firestore_service.credit_user(int(user_id), refund_amount)
            
            # Notify user
            chat_id = job_data.get('chat_id', user_id)
            telegram_service.send_message(int(chat_id),
                f"⚠️ Обработка вашего аудио не завершилась. "
                f"Возвращено {math.ceil(refund_amount)} минут на баланс.")
        
        cleaned_count += 1
        logging.info(f"Cleaned up stuck job: {job_id}")
    
    if cleaned_count > 0:
        logging.info(f"Cleaned up {cleaned_count} stuck jobs")
        # Notify admin
        telegram_service.send_message(services.OWNER_ID,
            f"🧹 Автоматическая очистка: {cleaned_count} застрявших задач")
    else:
        logging.info("No stuck jobs found during automatic cleanup")
    
    return f"Cleaned up {cleaned_count} stuck jobs", 200