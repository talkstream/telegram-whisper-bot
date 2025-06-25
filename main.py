import os
import json
import requests
import tempfile
import logging
import re
import math
import subprocess # Добавлен для вызова FFmpeg
import uuid

from openai import OpenAI
from google.cloud import secretmanager
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud import pubsub_v1
from datetime import datetime, time, timedelta
import pytz

import vertexai
from vertexai.generative_models import GenerativeModel

# Import services
from services import telegram as telegram_service
from services.firestore import FirestoreService
from services.audio import AudioService
from services.utility import UtilityService
from services.stats import StatsService

# Import handlers
from handlers import CommandRouter

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)

# --- КОНСТАНТЫ ---
PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')
DATABASE_ID = 'editorials-robot'
LOCATION = 'europe-west1' 
OWNER_ID = 775707
TRIAL_MINUTES = 15
MAX_MESSAGE_LENGTH = 4000
MAX_TELEGRAM_FILE_SIZE = 20 * 1024 * 1024 # 20 MB

# Для уведомлений о заявках
LAST_TRIAL_NOTIFICATION_TIMESTAMP_DOC_ID = "last_trial_notification_ts"
MIN_NOTIFICATION_INTERVAL_SECONDS = 1800 # 30 минут

# Pub/Sub для асинхронной обработки
AUDIO_PROCESSING_TOPIC = os.environ.get('AUDIO_PROCESSING_TOPIC', 'audio-processing-jobs')
USE_ASYNC_PROCESSING = os.environ.get('USE_ASYNC_PROCESSING', 'true').lower() == 'true'

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
SECRETS_LOADED = False
TELEGRAM_BOT_TOKEN = None
OPENAI_API_KEY = None
TELEGRAM_API_URL = None
TELEGRAM_FILE_URL = None
openai_client = None
db = None
firestore_service = None
audio_service = None
stats_service = None
metrics_service = None
publisher = None
command_router = None

# --- КОНСТАНТЫ ДЛЯ ПАКЕТОВ (Telegram Stars) ---
PRODUCT_PACKAGES = {
    "micro_10": {"title": "Промо-пакет 'Микро'", "description": "10 минут транскрибации", "payload": "buy_micro_10", "stars_amount": 10, "minutes": 10, "purchase_limit": 3},
    "start_50": {"title": "Пакет 'Старт'", "description": "50 минут транскрибации", "payload": "buy_start_50", "stars_amount": 75, "minutes": 50},
    "standard_200": {"title": "Пакет 'Стандарт'", "description": "200 минут транскрибации", "payload": "buy_standard_200", "stars_amount": 270, "minutes": 200},
    "profi_1000": {"title": "Пакет 'Профи'", "description": "1000 минут транскрибации", "payload": "buy_profi_1000", "stars_amount": 1150, "minutes": 1000},
    "max_8888": {"title": "Пакет 'MAX'", "description": "8888 минут транскрибации", "payload": "buy_max_8888", "stars_amount": 8800, "minutes": 8888},
}

# --- ИНИЦИАЛИЗАЦИЯ ---
def initialize():
    # ... (код инициализации без изменений) ...
    global SECRETS_LOADED, TELEGRAM_BOT_TOKEN, OPENAI_API_KEY
    global TELEGRAM_API_URL, TELEGRAM_FILE_URL, openai_client, db, firestore_service, audio_service, stats_service, metrics_service, publisher, command_router
    if SECRETS_LOADED: return True
    if not PROJECT_ID:
        logging.error("FATAL: GCP_PROJECT environment variable or fallback Project ID not set.")
        return False
    try:
        sm_client = secretmanager.SecretManagerServiceClient()
        def get_secret(secret_id):
            name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
            response = sm_client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8").strip()
        TELEGRAM_BOT_TOKEN = get_secret("telegram-bot-token")
        OPENAI_API_KEY = get_secret("openai-api-key")
        TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        TELEGRAM_FILE_URL = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"
        
        # Initialize services
        telegram_service.init_telegram_service(TELEGRAM_BOT_TOKEN)
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)
        firestore_service = FirestoreService(PROJECT_ID, DATABASE_ID)
        stats_service = StatsService(db)
        from services.metrics import MetricsService
        metrics_service = MetricsService(db)
        audio_service = AudioService(OPENAI_API_KEY, metrics_service)
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        
        # Initialize Pub/Sub publisher if async processing is enabled
        if USE_ASYNC_PROCESSING:
            publisher = pubsub_v1.PublisherClient()
            logging.info(f"Pub/Sub publisher initialized for topic: {AUDIO_PROCESSING_TOPIC}")
        
        # Initialize command router
        services_dict = {
            'firestore_service': firestore_service,
            'stats_service': stats_service,
            'metrics_service': metrics_service,
            'telegram_service': telegram_service.get_telegram_service(),
            'audio_service': audio_service,
            'get_user_data': get_user_data,
            'create_trial_request': create_trial_request,
            'get_pending_trial_requests': get_pending_trial_requests,
            'update_trial_request_status': update_trial_request_status,
            'get_all_users_for_admin': get_all_users_for_admin,
            'set_user_state': set_user_state,
            'UtilityService': UtilityService,
            'db': db
        }
        
        constants_dict = {
            'OWNER_ID': OWNER_ID,
            'TRIAL_MINUTES': TRIAL_MINUTES,
            'PRODUCT_PACKAGES': PRODUCT_PACKAGES
        }
        
        command_router = CommandRouter(services_dict, constants_dict)
        
        SECRETS_LOADED = True
        logging.info("Initialization successful (Secrets, Firestore & Vertex AI).")
        return True
    except Exception as e:
        logging.exception(f"FATAL: Could not initialize! Project ID: {PROJECT_ID}.")
        return False

# --- РАБОТА С TELEGRAM API ---
# Import functions from telegram service for backward compatibility
from services.telegram import (
    send_message,
    edit_message_text,
    send_document,
    get_file_path,
    download_file
)

# --- РАБОТА С PUB/SUB ---
def publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, status_message_id=None, is_batch_confirmation=False):
    """Publish audio processing job to Pub/Sub"""
    if not publisher or not SECRETS_LOADED:
        logging.error("Publisher not initialized")
        return None
        
    job_id = str(uuid.uuid4())
    
    # Create job document in Firestore
    if firestore_service:
        firestore_service.create_audio_job(job_id, {
            'job_id': job_id,
            'user_id': str(user_id),
            'user_name': user_name,  # Add user_name for display in /status
            'chat_id': chat_id,
            'status': 'pending',
            'created_at': firestore.SERVER_TIMESTAMP,
            'file_id': file_id,
            'file_size': file_size,
            'duration': duration
        })
    else:
        job_ref = db.collection('audio_jobs').document(job_id)
        job_ref.set({
            'job_id': job_id,
            'user_id': str(user_id),
            'user_name': user_name,  # Add user_name for display in /status
            'chat_id': chat_id,
            'status': 'pending',
            'created_at': firestore.SERVER_TIMESTAMP,
            'file_id': file_id,
            'file_size': file_size,
            'duration': duration
        })
    
    # Prepare message for Pub/Sub
    message_data = {
        'job_id': job_id,
        'user_id': user_id,
        'chat_id': chat_id,
        'file_id': file_id,
        'file_size': file_size,
        'duration': duration,
        'user_name': user_name,
        'status_message_id': status_message_id,
        'is_batch_confirmation': is_batch_confirmation
    }
    
    # Publish to Pub/Sub
    topic_path = publisher.topic_path(PROJECT_ID, AUDIO_PROCESSING_TOPIC)
    message_bytes = json.dumps(message_data).encode('utf-8')
    
    try:
        future = publisher.publish(topic_path, message_bytes)
        message_id = future.result()
        logging.info(f"Published audio job {job_id} to Pub/Sub with message ID: {message_id}")
        return job_id
    except Exception as e:
        logging.error(f"Failed to publish job to Pub/Sub: {e}")
        if firestore_service:
            firestore_service.update_audio_job(job_id, {'status': 'failed', 'error': str(e)})
        else:
            job_ref = db.collection('audio_jobs').document(job_id)
            job_ref.update({'status': 'failed', 'error': str(e)})
        return None

# --- РАБОТА С FIRESTORE ---
# ... (все функции без изменений) ...
def get_user_data(user_id):
    if not firestore_service:
        return None
    return firestore_service.get_user(user_id)
def create_or_update_user(user_id, name, balance_minutes_to_add=0, is_trial_approved=False, purchased_micro_package=False):
    # TODO: Migrate this complex function to use firestore_service when ready
    doc_ref = db.collection('users').document(str(user_id))
    doc = doc_ref.get()
    update_payload = {'last_seen': firestore.SERVER_TIMESTAMP} 
    if name: update_payload['first_name'] = name
    if not doc.exists:
        update_payload['added_at'] = firestore.SERVER_TIMESTAMP
        update_payload['balance_minutes'] = balance_minutes_to_add
        if is_trial_approved:
            update_payload['trial_status'] = 'approved'
        if purchased_micro_package:
            update_payload['micro_package_purchases'] = 1
        else:
            update_payload['micro_package_purchases'] = 0
        doc_ref.set(update_payload)
    else:
        current_data = doc.to_dict()
        if name and current_data.get('first_name') != name :
             pass 
        if balance_minutes_to_add != 0:
             update_payload['balance_minutes'] = firestore.Increment(balance_minutes_to_add)
        if is_trial_approved:
             update_payload['trial_status'] = 'approved'
        if purchased_micro_package:
            update_payload['micro_package_purchases'] = firestore.Increment(1)
        if update_payload: 
            doc_ref.update(update_payload)
def set_user_state(user_id, state_data=None):
    if firestore_service:
        firestore_service.set_user_state(user_id, state_data)
def get_user_state(user_id):
    if not firestore_service:
        return None
    return firestore_service.get_user_state(user_id)
def get_all_users_for_admin():
    if not firestore_service:
        return []
    return firestore_service.get_all_users()
def remove_user_from_system(user_id):
    if firestore_service:
        firestore_service.delete_user(user_id)
def log_transcription_attempt(user_id, name, size, duration_secs, status, char_count=0):
    if not firestore_service:
        return
    try:
        firestore_service.log_transcription({
            'user_id': str(user_id),
            'editor_name': name,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'file_size': size,
            'duration': duration_secs,
            'status': status,
            'char_count': char_count
        })
        logging.info(f"Logged attempt for {user_id}: {status}")
    except Exception as e:
        logging.error(f"Error logging attempt for {user_id}: {e}")
def log_payment(user_id, user_name, telegram_charge_id, provider_charge_id, stars_amount, minutes_credited, package_name):
    if not firestore_service:
        return
    try:
        # Log payment to database
        payment_data = {
            'user_id': str(user_id),
            'user_name': user_name,
            'telegram_payment_charge_id': telegram_charge_id,
            'provider_payment_charge_id': provider_charge_id,
            'stars_amount': stars_amount,
            'minutes_credited': minutes_credited,
            'package_name': package_name,
            'timestamp': firestore.SERVER_TIMESTAMP
        }
        firestore_service.log_payment(payment_data)
        logging.info(f"Logged payment for user {user_id}: {stars_amount} Stars for {minutes_credited} minutes.")
        
        # Queue notification for owner
        queue_payment_notification_for_owner(user_id, user_name, stars_amount, minutes_credited, package_name)
        
    except Exception as e:
        logging.error(f"Error logging payment for user {user_id}: {e}")
def log_oversized_file(user_id, user_name, file_id, reported_size, file_name=None, mime_type=None):
    if not firestore_service:
        return
    try:
        data_to_log = {
            'user_id': str(user_id),
            'user_name': user_name,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'reported_size': reported_size
        }
        if file_id: data_to_log['file_id'] = file_id
        if file_name: data_to_log['file_name'] = file_name
        if mime_type: data_to_log['mime_type'] = mime_type
        firestore_service.log_oversized_file(data_to_log)
        logging.info(f"Logged oversized file from user {user_id}, size: {reported_size}")
    except Exception as e:
        logging.error(f"Error logging oversized file for user {user_id}: {e}")

# --- TRIAL REQUESTS HELPERS ---
def create_trial_request(user_id, user_name):
    if not firestore_service:
        return False
    trial_request = firestore_service.get_trial_request(user_id)
    if not trial_request or trial_request.get('status') == 'denied_can_reapply':
        firestore_service.create_trial_request(user_id, {
            'user_id': str(user_id),
            'user_name': user_name,
            'request_timestamp': firestore.SERVER_TIMESTAMP,
            'status': 'pending'
        })
        # Queue notification for owner
        queue_trial_notification_for_owner(user_id, user_name, 'new')
        return True
    current_status = trial_request.get('status')
    if current_status in ['pending', 'pending_reconsideration']:
        return "already_pending"
    if current_status == 'approved':
        return "already_approved"
    return False
def get_pending_trial_requests():
    if not firestore_service:
        return []
    trial_requests = firestore_service.get_pending_trial_requests(limit=5)
    requests = []
    for user_id_str, data in trial_requests:
        requests.append({
            'id': user_id_str,
            'user_name': data.get('user_name'),
            'user_id_str': data.get('user_id_str'),
            'timestamp': data.get('timestamp')
        })
    return requests
def update_trial_request_status(user_id, status, admin_comment=None, reconsideration_text=None):
    if not firestore_service:
        return
    data_to_update = {'status': status}
    if admin_comment: data_to_update['admin_comment'] = admin_comment
    if reconsideration_text: data_to_update['reconsideration_text'] = reconsideration_text
    firestore_service.update_trial_request(user_id, data_to_update)

# --- NOTIFICATION HELPER ---
def check_and_notify_pending_trials(force_check=False):
    if not firestore_service or not OWNER_ID or not SECRETS_LOADED:
        return
    
    now = datetime.now(pytz.utc)
    last_notified_ts = firestore_service.get_last_trial_notification_timestamp()
    
    if not force_check and last_notified_ts and (now - last_notified_ts).total_seconds() < MIN_NOTIFICATION_INTERVAL_SECONDS:
        return
    
    try:
        if force_check:
            all_pending_docs = firestore_service.get_all_pending_trial_requests()
            count = len(all_pending_docs)
            if count > 0:
                send_message(OWNER_ID, f"🔔 Ежедневное напоминание: Есть необработанные заявки на пробный доступ ({count} шт.). Для просмотра: /review_trials")
                firestore_service.update_last_trial_notification_timestamp(daily_check=True)
        else:
            pending_requests = firestore_service.get_pending_trial_requests(limit=1)
            if pending_requests:
                send_message(OWNER_ID, "🔔 Появились новые заявки на пробный доступ! Для просмотра: /review_trials")
                firestore_service.update_last_trial_notification_timestamp()
    except Exception as e:
        logging.error(f"Error checking/notifying pending trials: {e}")

# --- PAYMENT NOTIFICATION SYSTEM ---
# In-memory storage for payment notifications (will be cleared on restart)
pending_payment_notifications = []
last_payment_notification_time = None

def queue_payment_notification_for_owner(user_id, user_name, stars_amount, minutes_credited, package_name):
    """Queue payment notification for batched sending to owner"""
    global pending_payment_notifications, last_payment_notification_time
    
    try:
        payment_info = {
            'user_id': user_id,
            'user_name': user_name,
            'stars_amount': stars_amount,
            'minutes_credited': minutes_credited,
            'package_name': package_name,
            'timestamp': datetime.now(pytz.utc)
        }
        
        pending_payment_notifications.append(payment_info)
        
        # If this is the first notification or it's been more than 10 minutes since last notification
        if not last_payment_notification_time or (datetime.now(pytz.utc) - last_payment_notification_time).total_seconds() > 600:
            # Send immediate notification for first payment
            if len(pending_payment_notifications) == 1:
                send_payment_notification_to_owner()
            # Otherwise, wait for more payments to accumulate
        
    except Exception as e:
        logging.error(f"Error queuing payment notification: {e}")

def send_payment_notification_to_owner():
    """Send accumulated payment notifications to owner"""
    global pending_payment_notifications, last_payment_notification_time
    
    if not pending_payment_notifications or not OWNER_ID or not SECRETS_LOADED:
        return
    
    try:
        # Copy and clear the list
        notifications_to_send = pending_payment_notifications[:]
        pending_payment_notifications = []
        last_payment_notification_time = datetime.now(pytz.utc)
        
        if len(notifications_to_send) == 1:
            # Single payment notification
            payment = notifications_to_send[0]
            msg = f"💰 <b>Новая покупка!</b>\n\n"
            msg += f"👤 {payment['user_name']} (ID: {payment['user_id']})\n"
            msg += f"📦 {payment['package_name']}\n"
            msg += f"⭐ {payment['stars_amount']} Stars\n"
            msg += f"⏱ {payment['minutes_credited']} минут"
        else:
            # Multiple payments - show summary
            total_stars = sum(p['stars_amount'] for p in notifications_to_send)
            total_minutes = sum(p['minutes_credited'] for p in notifications_to_send)
            
            msg = f"💰 <b>Сводка продаж ({len(notifications_to_send)} покупок)</b>\n\n"
            msg += f"<b>Итого:</b> {total_stars} ⭐ за {total_minutes} минут\n\n"
            msg += "<b>Детали:</b>\n"
            
            for payment in notifications_to_send:
                msg += f"• {payment['user_name']} - {payment['package_name']} ({payment['stars_amount']} ⭐)\n"
        
        send_message(OWNER_ID, msg, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Error sending payment notification to owner: {e}")

# --- TRIAL REQUEST NOTIFICATION SYSTEM ---
# In-memory storage for trial request notifications
pending_trial_notifications = []
last_trial_notification_time = None

def queue_trial_notification_for_owner(user_id, user_name, request_type='new'):
    """Queue trial request notification for batched sending to owner"""
    global pending_trial_notifications, last_trial_notification_time
    
    try:
        trial_info = {
            'user_id': user_id,
            'user_name': user_name,
            'request_type': request_type,  # 'new' or 'reconsideration'
            'timestamp': datetime.now(pytz.utc)
        }
        
        pending_trial_notifications.append(trial_info)
        
        # If this is the first notification or it's been more than 10 minutes since last notification
        if not last_trial_notification_time or (datetime.now(pytz.utc) - last_trial_notification_time).total_seconds() > 600:
            # Send immediate notification for first request
            if len(pending_trial_notifications) == 1:
                send_trial_notification_to_owner()
            # Otherwise, wait for more requests to accumulate
        
    except Exception as e:
        logging.error(f"Error queuing trial notification: {e}")

def send_trial_notification_to_owner():
    """Send accumulated trial request notifications to owner"""
    global pending_trial_notifications, last_trial_notification_time
    
    if not pending_trial_notifications or not OWNER_ID or not SECRETS_LOADED:
        return
    
    try:
        # Copy and clear the list
        notifications_to_send = pending_trial_notifications[:]
        pending_trial_notifications = []
        last_trial_notification_time = datetime.now(pytz.utc)
        
        if len(notifications_to_send) == 1:
            # Single request notification
            request = notifications_to_send[0]
            msg = f"🔔 <b>Новая заявка на пробный доступ!</b>\n\n"
            msg += f"👤 {request['user_name']} (ID: {request['user_id']})\n"
            if request['request_type'] == 'reconsideration':
                msg += f"📌 Тип: Запрос на пересмотр\n"
            msg += f"\nДля просмотра: /review_trials"
        else:
            # Multiple requests - show summary
            new_count = sum(1 for r in notifications_to_send if r['request_type'] == 'new')
            recon_count = sum(1 for r in notifications_to_send if r['request_type'] == 'reconsideration')
            
            msg = f"🔔 <b>Новые заявки на пробный доступ ({len(notifications_to_send)} шт.)</b>\n\n"
            if new_count > 0:
                msg += f"📋 Новых заявок: {new_count}\n"
            if recon_count > 0:
                msg += f"📌 Запросов на пересмотр: {recon_count}\n"
            msg += "\n<b>Детали:</b>\n"
            
            for request in notifications_to_send:
                type_emoji = "📌" if request['request_type'] == 'reconsideration' else "📋"
                msg += f"{type_emoji} {request['user_name']} (ID: {request['user_id']})\n"
            
            msg += f"\nДля просмотра: /review_trials"
        
        send_message(OWNER_ID, msg, parse_mode="HTML")
        
    except Exception as e:
        logging.error(f"Error sending trial notification to owner: {e}")

# --- UTILITY HELPERS ---
def is_authorized(user_id, user_data_from_db): # ... (без изменений)
    if user_id == OWNER_ID: return True
    if user_data_from_db and (user_data_from_db.get('balance_minutes', 0) > 0 or user_data_from_db.get('trial_status') == 'approved'):
        if user_data_from_db.get('trial_status') == 'approved' and user_data_from_db.get('balance_minutes', 0) <= 0: return False
        return True
    return False

# --- ГЛАВНАЯ ФУНКЦИЯ ОБРАБОТКИ ---
def handle_telegram_webhook(request):
    if not initialize():
        # ... (код без изменений)
        logging.error("Processing aborted due to initialization failure.")
        return "Internal Server Error", 500

    if request.method != "POST": return "Only POST method is allowed", 405

    try:
        update = request.get_json(silent=True)
        if not update: return "Bad Request", 400
        logging.info(f"Received update: {json.dumps(update)}")

        # --- ОБРАБОТКА СИСТЕМНЫХ ЗАПРОСОВ TELEGRAM (PAYMENTS) ---
        if "pre_checkout_query" in update:
            # ... (код без изменений)
            pre_checkout_query = update["pre_checkout_query"]
            query_id = pre_checkout_query["id"]
            
            # Use telegram service to answer pre-checkout query
            service = telegram_service.get_telegram_service()
            if service:
                service.answer_pre_checkout_query(query_id, ok=True)
            else:
                logging.error("Telegram service not initialized for pre_checkout_query")
            return "OK", 200
        
        if "message" in update and "successful_payment" in update["message"]:
            # ... (код без изменений)
            message_with_payment = update["message"]
            successful_payment = message_with_payment["successful_payment"]
            user_id_payment = message_with_payment["from"]["id"]
            user_name_payment = message_with_payment["from"].get("first_name", f"User_{user_id_payment}")
            stars_paid = successful_payment["total_amount"] 
            invoice_payload = successful_payment["invoice_payload"]
            telegram_charge_id = successful_payment["telegram_payment_charge_id"]
            provider_charge_id = successful_payment.get("provider_payment_charge_id", "") 
            package_info = None
            purchased_package_id = None
            for pkg_id_loop, pkg in PRODUCT_PACKAGES.items():
                if pkg["payload"] == invoice_payload:
                    package_info = pkg
                    purchased_package_id = pkg_id_loop
                    break
            if package_info:
                minutes_to_credit = package_info["minutes"]
                is_micro_purchase = purchased_package_id == "micro_10"
                create_or_update_user(user_id_payment, user_name_payment, minutes_to_credit, purchased_micro_package=is_micro_purchase)
                log_payment(user_id_payment, user_name_payment, telegram_charge_id, provider_charge_id, stars_paid, minutes_to_credit, package_info["title"])
                new_balance_data = get_user_data(user_id_payment)
                new_balance_minutes = new_balance_data.get("balance_minutes", 0) if new_balance_data else 0
                purchase_message = f"🎉 Оплата прошла успешно! Вам начислено {minutes_to_credit} минут. Ваш новый баланс: {math.floor(new_balance_minutes)} минут."
                if is_micro_purchase:
                    updated_user_data = get_user_data(user_id_payment)
                    purchases_count = updated_user_data.get("micro_package_purchases", 0) if updated_user_data else 0
                    limit_micro = PRODUCT_PACKAGES["micro_10"]["purchase_limit"]
                    if purchases_count < limit_micro:
                        purchase_message += f"\nВы можете приобрести пакет '{PRODUCT_PACKAGES['micro_10']['title']}' еще {limit_micro - purchases_count} раз(а)."
                    else:
                        purchase_message += f"\nЛимит на покупку пакета '{PRODUCT_PACKAGES['micro_10']['title']}' исчерпан."
                send_message(user_id_payment, purchase_message)
            else:
                logging.error(f"Unknown invoice_payload received: {invoice_payload} for user {user_id_payment}")
                send_message(user_id_payment, "Произошла ошибка при зачислении минут. Пожалуйста, обратитесь к администратору.")
                send_message(OWNER_ID, f"⚠️ Ошибка зачисления минут! Неизвестный payload: {invoice_payload} от user_id: {user_id_payment}, stars_paid: {stars_paid}")
            return "OK", 200

        # --- ОБРАБОТКА НАЖАТИЙ НА КНОПКИ (CALLBACK_QUERY) ---
        if "callback_query" in update:
            # ... (код без изменений)
            callback_query = update["callback_query"]
            from_user_cb = callback_query["from"]
            user_id_cb = from_user_cb["id"]
            user_name_cb = from_user_cb.get("first_name", f"User_{user_id_cb}")
            callback_data = callback_query["data"]
            original_message = callback_query["message"]
            original_message_id = original_message["message_id"]
            original_chat_id = original_message["chat"]["id"]
            parts = callback_data.split('_')
            action = parts[0]
            if action == "requesttrial":
                target_user_id_req_str = parts[1]
                target_user_name_req = parts[2] if len(parts) > 2 else user_name_cb
                status = create_trial_request(int(target_user_id_req_str), target_user_name_req)
                if status == True: edit_message_text(original_chat_id, original_message_id, "Спасибо! Ваша заявка принята и будет рассмотрена вручную, обычно в течение суток. Вы получите уведомление.")
                elif status == "already_pending": edit_message_text(original_chat_id, original_message_id, "Вы уже подали заявку. Ожидайте рассмотрения.")
                elif status == "already_approved": edit_message_text(original_chat_id, original_message_id, "Вам уже одобрен пробный доступ.")
                else: edit_message_text(original_chat_id, original_message_id, "Вы уже обращались за пробным доступом. Для получения информации обратитесь к администратору.")
            elif action == "selectpkg":
                package_full_id = parts[1] + "_" + parts[2] if len(parts) > 2 else parts[1]
                package_to_buy = PRODUCT_PACKAGES.get(package_full_id)
                if package_to_buy:
                    prices_list = [{"label": package_to_buy["description"], "amount": package_to_buy["stars_amount"]}]
                    
                    # Use telegram service to send invoice
                    service = telegram_service.get_telegram_service()
                    if service:
                        result = service.send_invoice(
                            chat_id=user_id_cb,
                            title=package_to_buy["title"],
                            description=package_to_buy["description"],
                            payload=package_to_buy["payload"],
                            currency="XTR",
                            prices=prices_list,
                            start_parameter=f"buy_{package_full_id}"
                        )
                        if result:
                            edit_message_text(original_chat_id, original_message_id, "Счет для оплаты сформирован и отправлен вам.")
                        else:
                            send_message(user_id_cb, "Не удалось создать счет. Попробуйте позже или обратитесь к администратору.")
                    else:
                        logging.error("Telegram service not initialized for sending invoice")
                        send_message(user_id_cb, "Не удалось создать счет. Попробуйте позже или обратитесь к администратору.")
                else:
                    logging.warning(f"Unknown package ID in callback: {package_full_id}")
                    send_message(user_id_cb, "Выбран неизвестный пакет.")
            elif user_id_cb == OWNER_ID:
                # Handle new format callbacks (approve_trial_, deny_trial_)
                if action == "approve" and len(parts) >= 2 and parts[1] == "trial":
                    target_user_id = int(parts[2]) if len(parts) > 2 else None
                    if target_user_id:
                        # Get user info from trial request
                        trial_req_doc = db.collection('trial_requests').document(str(target_user_id)).get()
                        if trial_req_doc.exists:
                            trial_data = trial_req_doc.to_dict()
                            target_user_name = trial_data.get('user_name', f"User_{target_user_id}")
                            
                            # Credit the user with trial minutes
                            if firestore_service:
                                firestore_service.update_user_balance(target_user_id, TRIAL_MINUTES)
                                firestore_service.update_user_trial_status(target_user_id, 'approved')
                                update_trial_request_status(target_user_id, "approved", admin_comment="Approved via inline button")
                                
                                # Delete the trial request
                                trial_req_doc.reference.delete()
                                
                                # Update the message
                                new_text = f"✅ <b>Заявка одобрена</b>\n\n"
                                new_text += f"👤 Пользователь: {target_user_name}\n"
                                new_text += f"🆔 ID: {target_user_id}\n"
                                new_text += f"💰 Начислено: {TRIAL_MINUTES} минут"
                                edit_message_text(original_chat_id, original_message_id, new_text, parse_mode="HTML")
                                
                                # Notify the user
                                send_message(target_user_id, f"🎉 Поздравляем! Ваша заявка на пробный доступ одобрена. Вам начислено {TRIAL_MINUTES} минут.")
                        else:
                            edit_message_text(original_chat_id, original_message_id, "❌ Заявка не найдена или уже обработана.")
                    return "OK", 200
                
                elif action == "deny" and len(parts) >= 2 and parts[1] == "trial":
                    target_user_id = int(parts[2]) if len(parts) > 2 else None
                    if target_user_id:
                        # Get user info from trial request
                        trial_req_doc = db.collection('trial_requests').document(str(target_user_id)).get()
                        if trial_req_doc.exists:
                            trial_data = trial_req_doc.to_dict()
                            target_user_name = trial_data.get('user_name', f"User_{target_user_id}")
                            
                            # Set state for denial comment
                            set_user_state(OWNER_ID, {
                                'state': 'awaiting_denial_comment', 
                                'target_user_id': target_user_id, 
                                'target_user_name': target_user_name, 
                                'admin_message_id': original_message_id
                            })
                            
                            # Update the message
                            new_text = f"❓ <b>Ожидание комментария для отказа</b>\n\n"
                            new_text += f"👤 Пользователь: {target_user_name}\n"
                            new_text += f"🆔 ID: {target_user_id}\n\n"
                            new_text += "Введите причину отказа или /cancel для отмены."
                            edit_message_text(original_chat_id, original_message_id, new_text, parse_mode="HTML")
                            send_message(OWNER_ID, f"Введите причину отказа для {target_user_name} (ID: {target_user_id}). Отправьте /cancel для отмены.")
                        else:
                            edit_message_text(original_chat_id, original_message_id, "❌ Заявка не найдена или уже обработана.")
                    return "OK", 200
                
                # Keep old format for backward compatibility
                target_user_id_str = parts[1] if len(parts) > 1 else None
                target_user_name_from_cb_parts = parts[2] if len(parts) > 2 else None
                if not target_user_id_str: logging.warning(f"Callback for owner missing target_user_id_str: {callback_data}"); return "OK", 200
                target_user_id = int(target_user_id_str)
                target_user_name_final = target_user_name_from_cb_parts
                if not target_user_name_final:
                    trial_req_doc = db.collection('trial_requests').document(str(target_user_id)).get()
                    target_user_name_final = trial_req_doc.to_dict().get('user_name', f"User_{target_user_id}") if trial_req_doc.exists else f"User_{target_user_id}"
                if action == "approvetrial":
                    create_or_update_user(target_user_id, target_user_name_final, TRIAL_MINUTES, is_trial_approved=True)
                    update_trial_request_status(target_user_id, "approved")
                    new_text = f"✅ Доступ для {target_user_name_final} (ID: {target_user_id}) одобрен на {TRIAL_MINUTES} мин."
                    edit_message_text(original_chat_id, original_message_id, new_text)
                    send_message(target_user_id, f"🎉 Ваш пробный доступ одобрен! Баланс: {TRIAL_MINUTES} минут. Можете отправлять аудио.")
                elif action == "denytrial":
                    set_user_state(OWNER_ID, {'state': 'awaiting_denial_comment', 'target_user_id': target_user_id, 'target_user_name': target_user_name_final, 'admin_message_id': original_message_id})
                    edit_message_text(original_chat_id, original_message_id, f"Заявка для {target_user_name_final} (ID: {target_user_id}) ожидает комментария для отказа.")
                    send_message(OWNER_ID, f"Введите причину отказа для {target_user_name_final} (ID: {target_user_id}). Отправьте /cancel для отмены.")
            elif action == "reconsider":
                target_user_id = int(parts[1])
                update_trial_request_status(target_user_id, "pending_reconsideration")
                set_user_state(target_user_id, {'state': 'awaiting_reconsideration_text'})
                edit_message_text(original_chat_id, original_message_id, "Пожалуйста, опишите, почему вы считаете, что вам нужен доступ, или предоставьте дополнительную информацию. Ваше сообщение будет передано администратору.")
            return "OK", 200

        # --- ОБРАБОТКА ОБЫЧНЫХ СООБЩЕНИЙ ---
        if "message" in update:
            # ... (Весь остальной код, включая FFmpeg, как в предыдущей версии)
            message = update["message"]
            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]
            user_name = message["from"].get("first_name", f"User_{user_id}")
            text = message.get("text", "")
            user_data = get_user_data(user_id)
            owner_state_doc = get_user_state(OWNER_ID) if user_id == OWNER_ID else None
            current_user_state_doc = get_user_state(user_id) if user_id != OWNER_ID else None
            
            # Debug logging for all text commands
            if text.startswith("/"):
                logging.info(f"Received command '{text}' from user {user_id} ({user_name})")

            if user_id == OWNER_ID and owner_state_doc and owner_state_doc.get('state') == 'awaiting_denial_comment':
                # ... (код без изменений)
                target_user_id_for_denial = owner_state_doc.get('target_user_id')
                target_user_name_for_denial = owner_state_doc.get('target_user_name')
                admin_original_message_id = owner_state_doc.get('admin_message_id')
                admin_comment = text
                set_user_state(OWNER_ID, None)
                if text.lower() == '/cancel':
                    send_message(OWNER_ID, "Отмена ввода комментария. Заявка остается в ожидании.")
                    if admin_original_message_id:
                         keyboard = {"inline_keyboard": [[{"text": "✅ Одобрить", "callback_data": f"approve_trial_{target_user_id_for_denial}"},{"text": "❌ Отклонить", "callback_data": f"deny_trial_{target_user_id_for_denial}"}]]}
                         msg = f"📋 <b>Заявка на пробный доступ</b>\n\n"
                         msg += f"👤 Пользователь: {target_user_name_for_denial}\n"
                         msg += f"🆔 ID: {target_user_id_for_denial}"
                         edit_message_text(OWNER_ID, admin_original_message_id, msg, reply_markup=keyboard, parse_mode="HTML")
                else:
                    update_trial_request_status(target_user_id_for_denial, "denied_with_comment", admin_comment=admin_comment)
                    # Delete the trial request after denial
                    if db:
                        db.collection('trial_requests').document(str(target_user_id_for_denial)).delete()
                    reconsider_keyboard = {"inline_keyboard": [[{"text": "Запросить пересмотр", "callback_data": f"reconsider_{target_user_id_for_denial}"}]]}
                    send_message(target_user_id_for_denial, f"К сожалению, в пробном доступе отказано.\nПричина: {admin_comment}", reply_markup=reconsider_keyboard)
                    send_message(OWNER_ID, f"Отказ для {target_user_name_for_denial} с комментарием отправлен.")
                    if admin_original_message_id: 
                        edit_message_text(OWNER_ID, admin_original_message_id, f"❌ <b>Заявка отклонена</b>\n\n👤 Пользователь: {target_user_name_for_denial}\n🆔 ID: {target_user_id_for_denial}\n📝 Причина: {admin_comment}", parse_mode="HTML")
                return "OK", 200
            
            if user_id != OWNER_ID and current_user_state_doc and current_user_state_doc.get('state') == 'awaiting_reconsideration_text':
                # ... (код без изменений)
                reconsideration_text_from_user = text
                set_user_state(user_id, None)
                update_trial_request_status(user_id, "pending_reconsideration", reconsideration_text=reconsideration_text_from_user)
                # Queue notification for owner
                queue_trial_notification_for_owner(user_id, user_name, 'reconsideration')
                send_message(user_id, "Ваш запрос на пересмотр отправлен администратору.")
                return "OK", 200
            
            # --- COMMAND ROUTER ---
            if text.startswith("/") and command_router:
                update_data = {
                    'text': text,
                    'user_id': user_id,
                    'chat_id': chat_id,
                    'user_data': user_data,
                    'user_name': user_name,
                    'message': message
                }
                
                result = command_router.route(update_data)
                if result:
                    return result
            

            # --- ПРОВЕРКА АВТОРИЗАЦИИ И ОБРАБОТКА АУДИО ---
            # ... (вся остальная логика как была, с FFmpeg)
            if not user_data:
                send_message(chat_id, "Пожалуйста, сначала отправьте /start или /trial, чтобы запросить доступ.")
                return "OK", 200
            if not is_authorized(user_id, user_data):
                 trial_request_data_doc = db.collection('trial_requests').document(str(user_id)).get()
                 trial_request_data = trial_request_data_doc.to_dict() if trial_request_data_doc.exists else None
                 if trial_request_data and trial_request_data.get('status') == 'pending':
                     send_message(chat_id, "Ваша заявка на пробный доступ еще на рассмотрении. Пожалуйста, ожидайте.")
                 elif trial_request_data and trial_request_data.get('status') == 'pending_reconsideration':
                      send_message(chat_id, "Ваш запрос на пересмотр заявки находится у администратора. Ожидайте ответа.")
                 else:
                     send_message(chat_id, "У вас нет доступа или закончился баланс. Для получения доступа отправьте /trial или пополните баланс через /buy_minutes.")
                 return "OK", 200
            
            balance = user_data.get('balance_minutes', 0) if user_id != OWNER_ID else float('inf')
            
            # Check for media group (batch files)
            media_group_id = message.get("media_group_id")
            
            file_id, file_size, duration = None, 0, 0
            original_file_name, original_mime_type = None, None
            audio, voice, document = message.get("audio"), message.get("voice"), message.get("document")
            if audio: 
                file_id, file_size, duration = audio["file_id"], audio.get("file_size", 0), audio.get("duration", 0)
                original_file_name, original_mime_type = audio.get("file_name"), audio.get("mime_type")
            elif voice: 
                file_id, file_size, duration = voice["file_id"], voice.get("file_size", 0), voice.get("duration", 0)
                original_mime_type = voice.get("mime_type")
            elif document and document.get("mime_type", "").startswith("audio/"):
                file_id, file_size = document["file_id"], document.get("file_size", 0)
                original_file_name, original_mime_type = document.get("file_name"), document.get("mime_type")
                duration = document.get("duration", 0)

            if file_id:
                if file_size and file_size > MAX_TELEGRAM_FILE_SIZE:
                    log_oversized_file(user_id, user_name, file_id, file_size, original_file_name, original_mime_type)
                    oversized_message = f"""⚠️ Файл '<b>{original_file_name or 'Без имени'}</b>' ({UtilityService.format_size(file_size)}) превышает лимит в 20 МБ для автоматической обработки через Telegram.

Пожалуйста, попробуйте один из следующих вариантов:
• Сжать файл или перекодировать его в другой формат (например, MP3 с меньшим битрейтом).
• Разделить аудио на несколько частей менее 20 МБ каждая.
• Если вам регулярно нужно обрабатывать большие файлы, напишите @nafigator для обсуждения индивидуальных условий."""
                    send_message(chat_id, oversized_message, parse_mode="HTML")
                    return "OK", 200
                
                duration_minutes = math.ceil(duration / 60) if duration > 0 else 1
                if user_id != OWNER_ID and balance < duration_minutes:
                    send_message(chat_id, f"❌ Недостаточно средств. Длительность файла: ~{duration_minutes} мин. Ваш баланс: {math.floor(balance)} мин.")
                    return "OK", 200
                
                # In async mode, minutes will be deducted by audio processor after successful processing
                # In sync mode, we deduct here
                
                # Check if async processing is enabled
                if USE_ASYNC_PROCESSING and publisher:
                    # Check if this is part of a batch
                    batch_indicator = ""
                    if media_group_id:
                        # Track batch files in user state
                        batch_state = get_user_state(user_id) or {}
                        batch_files = batch_state.get('batch_files', {})
                        
                        if media_group_id not in batch_files:
                            batch_files[media_group_id] = []
                        
                        batch_files[media_group_id].append({
                            'file_name': original_file_name or f"Файл {len(batch_files[media_group_id]) + 1}",
                            'duration': duration,
                            'duration_minutes': duration_minutes
                        })
                        
                        batch_state['batch_files'] = batch_files
                        set_user_state(user_id, batch_state)
                        
                        batch_indicator = f"📦 Файл {len(batch_files[media_group_id])} из нескольких\n"
                        
                        # For batch files after the first, don't create individual status messages
                        if len(batch_files[media_group_id]) > 1:
                            # Just send a simple confirmation
                            simple_msg = f"📎 Файл {len(batch_files[media_group_id])}\n"
                            if original_file_name:
                                simple_msg += f"{original_file_name}\n"
                            simple_msg += f"⏱ {UtilityService.format_duration(duration)}"
                            confirmation_msg = send_message(chat_id, simple_msg)
                            confirmation_message_id = confirmation_msg.get('result', {}).get('message_id') if confirmation_msg else None
                            
                            # Publish job with confirmation message ID to delete it later
                            job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, confirmation_message_id, is_batch_confirmation=True)
                            if job_id:
                                logging.info(f"Batch audio job {job_id} published for user {user_id}")
                            else:
                                send_message(chat_id, '❌ Ошибка: Не удалось добавить файл в очередь.')
                            return "OK", 200
                    
                    # Create cleaner initial message
                    if batch_indicator:
                        file_info_msg = f"{batch_indicator}"
                    else:
                        file_info_msg = "📎 Файл получен\n"
                    
                    if original_file_name:
                        file_info_msg += f"{original_file_name}\n"
                    file_info_msg += f"⏱ {UtilityService.format_duration(duration)} • {UtilityService.format_size(file_size)}\n"
                    file_info_msg += f"💳 Спишется {duration_minutes} мин.\n\n"
                    
                    # Check queue first
                    queue_count = firestore_service.count_pending_jobs() if firestore_service else 0
                    if queue_count > 1:
                        file_info_msg += f"📊 В очереди: {UtilityService.pluralize_russian(queue_count, 'файл', 'файла', 'файлов')}\n"
                    file_info_msg += "⏳ Обрабатываю..."
                    
                    # Send initial status message
                    status_msg = send_message(chat_id, file_info_msg, parse_mode="HTML")
                    status_message_id = status_msg.get('result', {}).get('message_id') if status_msg else None
                    
                    # Publish job to Pub/Sub
                    job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, status_message_id)
                    
                    if job_id:
                        logging.info(f"Audio job {job_id} published for user {user_id}")
                        
                        # No need to update queue info as it's already included
                    else:
                        send_message(chat_id, '❌ Ошибка: Не удалось начать обработку файла.')
                        # Refund the minutes
                        if user_id != OWNER_ID:
                            create_or_update_user(user_id, user_name, duration_minutes)
                        log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_publish')
                    
                    return "OK", 200
                
                # Fallback to synchronous processing
                file_info_msg = "📎 Файл получен\n"
                if original_file_name:
                    file_info_msg += f"{original_file_name}\n"
                file_info_msg += f"⏱ {format_duration(duration)} • {format_size(file_size)}\n"
                file_info_msg += f"💳 Спишется {duration_minutes} мин.\n\n"
                file_info_msg += "⏳ Обрабатываю..."
                send_message(chat_id, file_info_msg, parse_mode="HTML")
                tg_file_path = get_file_path(file_id)
                if not tg_file_path:
                    send_message(chat_id, '❌ Ошибка: Не удалось получить информацию о файле.');
                    if user_id != OWNER_ID: 
                        create_or_update_user(user_id, user_name, duration_minutes)  # Refund
                        log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_getinfo')
                    return "OK", 200
                local_audio_path = download_file(tg_file_path)
                if not local_audio_path:
                    send_message(chat_id, '❌ Ошибка: Не удалось скачать файл.')
                    if user_id != OWNER_ID: 
                        create_or_update_user(user_id, user_name, duration_minutes)  # Refund
                        log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_download')
                    return "OK", 200

                # Convert audio to MP3
                converted_mp3_path = None
                if audio_service:
                    logging.info(f"Converting audio using AudioService: {local_audio_path}")
                    converted_mp3_path = audio_service.convert_to_mp3(local_audio_path)
                    if os.path.exists(local_audio_path): os.remove(local_audio_path)
                else:
                    # Fallback to legacy FFmpeg implementation
                    converted_mp3_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3", dir='/tmp').name
                    logging.info(f"Attempting to convert {local_audio_path} to {converted_mp3_path}")
                    # Use consistent MP3 encoding for reliability: 128k bitrate, 44.1kHz sample rate
                    ffmpeg_command = ['ffmpeg', '-y', '-i', local_audio_path, '-b:a', '128k', '-ar', '44100', converted_mp3_path]
                    try:
                        process = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True, timeout=60) # Таймаут на конвертацию
                        logging.info(f"FFmpeg conversion successful. STDOUT: {process.stdout}")
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                        logging.error(f"FFmpeg conversion failed. STDERR: {getattr(e, 'stderr', 'Timeout')}")
                        converted_mp3_path = None
                    finally:
                        if os.path.exists(local_audio_path): os.remove(local_audio_path)
                
                if not converted_mp3_path:
                    send_message(chat_id, '❌ Внутренняя ошибка: не удалось обработать аудиокодек файла. Попробуйте другой файл или формат.')
                    if os.path.exists(converted_mp3_path): os.remove(converted_mp3_path)
                    log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_codec')
                    return "OK", 200

                transcribed_text = audio_service.transcribe_audio(converted_mp3_path) if audio_service else None
                if os.path.exists(converted_mp3_path): os.remove(converted_mp3_path)
                
                if transcribed_text:
                    # Deduct minutes after successful transcription for all users
                    create_or_update_user(user_id, user_name, -duration_minutes)
                    new_balance = balance - duration_minutes
                    if user_id != OWNER_ID:
                        send_message(chat_id, f"Распознавание завершено. Списано {duration_minutes} мин. Остаток: {math.floor(new_balance)} мин.")
                    else:
                        send_message(chat_id, f"Распознавание завершено (админ). Списано {duration_minutes} мин.")
                    
                    formatted_text = audio_service.format_text_with_gemini(transcribed_text) if audio_service else transcribed_text
                    char_count = len(formatted_text)
                    log_transcription_attempt(user_id, user_name, file_size, duration, 'success', char_count)
                    
                    caption = UtilityService.get_first_sentence(formatted_text)
                    if len(caption) > 1024: caption = caption[:1021] + "..."
                    
                    # Get user settings
                    settings = firestore_service.get_user_settings(user_id) if firestore_service else {'use_code_tags': False}
                    use_code_tags = settings.get('use_code_tags', False)
                    
                    if len(formatted_text) > MAX_MESSAGE_LENGTH:
                        file_name = UtilityService.get_moscow_time_str() + ".txt"
                        temp_txt_path = os.path.join('/tmp', file_name)
                        try:
                            with open(temp_txt_path, 'w', encoding='utf-8') as f: f.write(formatted_text)
                            send_document(chat_id, temp_txt_path, caption=caption)
                            if os.path.exists(temp_txt_path): os.remove(temp_txt_path)
                        except Exception as e:
                             logging.error(f"Error creating/sending txt file: {e}")
                             # Format text based on user preference
                             if use_code_tags:
                                 error_text = f"❌ Ошибка при создании файла, отправляю как текст:\n<code>{UtilityService.escape_html(formatted_text[:MAX_MESSAGE_LENGTH])}...</code>"
                             else:
                                 error_text = f"❌ Ошибка при создании файла, отправляю как текст:\n{formatted_text[:MAX_MESSAGE_LENGTH]}..."
                             send_message(chat_id, error_text, "HTML" if use_code_tags else None)
                             if os.path.exists(temp_txt_path): os.remove(temp_txt_path)
                    else:
                        # Format text based on user preference
                        if use_code_tags:
                            send_message(chat_id, f"<code>{UtilityService.escape_html(formatted_text)}</code>", parse_mode="HTML")
                        else:
                            send_message(chat_id, formatted_text)
                else:
                    send_message(chat_id, '❌ Не удалось распознать аудио.')
                    log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_transcribe')
                return "OK", 200

            if user_id != OWNER_ID and not text.startswith('/'):
                 send_message(chat_id, "Пожалуйста, отправьте аудиофайл. Ваш баланс: " + str(math.floor(balance)) + " мин.")
            elif user_id == OWNER_ID and not text.startswith('/') and not file_id:
                pass 
            return "OK", 200

        logging.info(f"Update not handled: {json.dumps(update)}")
        return "OK", 200

    except Exception as e:
        logging.exception("An unhandled error occurred in webhook processing:")
        if OWNER_ID and SECRETS_LOADED:
            try: send_message(OWNER_ID, f"⚠️ Произошла критическая ошибка в боте: {e}")
            except: pass
        return "Internal Server Error", 500

# Warmup handler for App Engine
def handle_warmup(request):
    """Handle warmup requests from App Engine"""
    start_time = datetime.now()
    
    # Initialize all services
    if not initialize():
        logging.error("Failed to initialize during warmup")
        return "Failed to initialize", 500
    
    # Perform a test query to warm up Firestore connection
    try:
        if firestore_service:
            # Simple read to warm up the connection
            firestore_service.db.collection('users').limit(1).get()
            logging.info("Firestore connection warmed up")
    except Exception as e:
        logging.warning(f"Failed to warm up Firestore: {e}")
    
    # Log warmup completion time
    duration = (datetime.now() - start_time).total_seconds()
    logging.info(f"Warmup completed in {duration:.2f} seconds")
    
    return "OK", 200

# Health check handler
def handle_health(request):
    """Handle health check requests"""
    return "healthy", 200

# Automatic cleanup handler
def handle_cleanup_stuck_jobs(request):
    """Handle automatic cleanup of stuck jobs via cron"""
    try:
        # Only allow requests from App Engine cron
        if request.headers.get('X-Appengine-Cron') != 'true':
            logging.warning("Cleanup request from non-cron source")
            return "Forbidden", 403
        
        # Initialize if needed
        if not initialize():
            logging.error("Failed to initialize for cleanup")
            return "Failed to initialize", 500
        
        if not firestore_service:
            logging.error("Firestore service not available for cleanup")
            return "Service unavailable", 503
        
        # Get stuck jobs (older than 1 hour)
        stuck_jobs = firestore_service.get_stuck_jobs(hours_threshold=1)
        
        if not stuck_jobs:
            logging.info("No stuck jobs found during automatic cleanup")
            return "OK - No stuck jobs", 200
        
        # Log detailed information about stuck jobs before cleanup
        logging.warning(f"Found {len(stuck_jobs)} stuck jobs during automatic cleanup")
        
        for job in stuck_jobs:
            job_data = job.to_dict()
            logging.warning(f"Stuck job details - ID: {job.id}, "
                          f"User: {job_data.get('user_id', 'unknown')}, "
                          f"Status: {job_data.get('status')}, "
                          f"Duration: {job_data.get('duration', 0)}s, "
                          f"Created: {job_data.get('created_at')}, "
                          f"File ID: {job_data.get('file_id', 'unknown')[:20]}..., "
                          f"File Size: {job_data.get('file_size', 0)} bytes")
        
        # Clean up stuck jobs
        cleaned_count, cleaned_jobs = firestore_service.cleanup_stuck_jobs(hours_threshold=1)
        
        # Calculate total duration from cleaned jobs
        total_duration = sum(job.get('duration', 0) for job in cleaned_jobs)
        
        summary = f"Automatic cleanup completed: {cleaned_count} jobs cleaned, " \
                 f"total duration: {total_duration}s ({total_duration/60:.1f} min)"
        logging.info(summary)
        
        # Notify admin if significant number of stuck jobs
        if cleaned_count >= 5 and OWNER_ID and SECRETS_LOADED:
            try:
                send_message(OWNER_ID, 
                    f"🔧 Автоматическая очистка выполнена\n\n"
                    f"Удалено зависших задач: {cleaned_count}\n"
                    f"Общая длительность: {total_duration/60:.1f} мин.\n\n"
                    f"Проверьте логи для деталей.")
            except Exception as e:
                logging.error(f"Failed to notify admin about cleanup: {e}")
        
        return f"OK - {summary}", 200
        
    except Exception as e:
        logging.exception(f"Error during automatic cleanup: {e}")
        return "Internal Server Error", 500

def handle_payment_notifications(request):
    """Handle periodic payment notification sending"""
    try:
        if not initialize():
            return "Service unavailable", 503
        
        global pending_payment_notifications, last_payment_notification_time
        
        # Check if there are pending notifications and if enough time has passed
        if pending_payment_notifications and last_payment_notification_time:
            time_since_last = (datetime.now(pytz.utc) - last_payment_notification_time).total_seconds()
            
            # Send if it's been more than 1 hour since last notification
            if time_since_last >= 3600:  # 1 hour
                logging.info(f"Sending {len(pending_payment_notifications)} pending payment notifications")
                send_payment_notification_to_owner()
        
        return "OK", 200
        
    except Exception as e:
        logging.exception(f"Error handling payment notifications: {e}")
        return "Internal Server Error", 500

def handle_trial_notifications(request):
    """Handle periodic trial request notification sending"""
    try:
        if not initialize():
            return "Service unavailable", 503
        
        global pending_trial_notifications, last_trial_notification_time
        
        # Check if there are pending notifications and if enough time has passed
        if pending_trial_notifications and last_trial_notification_time:
            time_since_last = (datetime.now(pytz.utc) - last_trial_notification_time).total_seconds()
            
            # Send if it's been more than 1 hour since last notification
            if time_since_last >= 3600:  # 1 hour
                logging.info(f"Sending {len(pending_trial_notifications)} pending trial notifications")
                send_trial_notification_to_owner()
        
        return "OK", 200
        
    except Exception as e:
        logging.exception(f"Error handling trial notifications: {e}")
        return "Internal Server Error", 500

# Import Flask for WSGI compatibility
from flask import Flask, request

# Create Flask application
app = Flask(__name__)

@app.route('/_ah/warmup')
def warmup():
    """Handle warmup requests"""
    return handle_warmup(request)

@app.route('/health')
@app.route('/_ah/health')
def health():
    """Handle health check requests"""
    return handle_health(request)

@app.route('/', methods=['POST'])
def webhook():
    """Handle Telegram webhook"""
    return handle_telegram_webhook(request)

@app.route('/cleanup_stuck_jobs')
def cleanup_stuck_jobs():
    """Handle automatic cleanup of stuck jobs"""
    return handle_cleanup_stuck_jobs(request)

@app.route('/send_payment_notifications')
def send_payment_notifications():
    """Handle periodic sending of payment notifications"""
    return handle_payment_notifications(request)

@app.route('/send_trial_notifications')
def send_trial_notifications():
    """Handle periodic sending of trial request notifications"""
    return handle_trial_notifications(request)

# For local testing
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)