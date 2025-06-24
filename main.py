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
publisher = None

# --- КОНСТАНТЫ ДЛЯ ПАКЕТОВ (Telegram Stars) ---
PRODUCT_PACKAGES = {
    "micro_10": {"title": "Промо-пакет 'Микро'", "description": "10 минут транскрибации", "payload": "buy_micro_10", "stars_amount": 10, "minutes": 10, "purchase_limit": 3},
    "starter_60": {"title": "Пакет 'Стартовый'", "description": "60 минут транскрибации", "payload": "buy_starter_60", "stars_amount": 240, "minutes": 60},
    "editor_180": {"title": "Пакет 'Редактор'", "description": "180 минут (Экономия ~5%)", "payload": "buy_editor_180", "stars_amount": 680, "minutes": 180},
    "lite_600": {"title": "Пакет 'Редакция Lite'", "description": "600 минут (Экономия ~12%)", "payload": "buy_lite_600", "stars_amount": 2100, "minutes": 600},
    "pro_1500": {"title": "Пакет 'Редакция Pro'", "description": "1500 минут (Экономия 20%)", "payload": "buy_pro_1500", "stars_amount": 4800, "minutes": 1500},
}

# --- ИНИЦИАЛИЗАЦИЯ ---
def initialize():
    # ... (код инициализации без изменений) ...
    global SECRETS_LOADED, TELEGRAM_BOT_TOKEN, OPENAI_API_KEY
    global TELEGRAM_API_URL, TELEGRAM_FILE_URL, openai_client, db, firestore_service, audio_service, publisher
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
        audio_service = AudioService(OPENAI_API_KEY)
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        
        # Initialize Pub/Sub publisher if async processing is enabled
        if USE_ASYNC_PROCESSING:
            publisher = pubsub_v1.PublisherClient()
            logging.info(f"Pub/Sub publisher initialized for topic: {AUDIO_PROCESSING_TOPIC}")
        
        SECRETS_LOADED = True
        logging.info("Initialization successful (Secrets, Firestore & Vertex AI).")
        return True
    except Exception as e:
        logging.exception(f"FATAL: Could not initialize! Project ID: {PROJECT_ID}.")
        return False

# --- ФУНКЦИЯ ФОРМАТИРОВАНИЯ GEMINI ---
def format_text_with_gemini(text_to_format: str) -> str:
    # ... (код без изменений) ...
    if audio_service:
        return audio_service.format_text_with_gemini(text_to_format)
    # Fallback to legacy implementation
    try:
        model = GenerativeModel("gemini-2.5-flash")
        prompt = f"""
        Твоя задача — отформатировать следующий транскрипт устной речи, улучшив его читаемость, но полностью сохранив исходный смысл, стиль и лексику автора.
        1.  **Формирование абзацев:** Объединяй несколько (обычно от 2 до 5) связанных по теме предложений в один абзац. Начинай новый абзац только при явной смене микро-темы или при переходе к новому аргументу в рассуждении. Избегай создания слишком коротких абзацев из одного предложения.
        2.  **Обработка предложений:** Сохраняй оригинальную структуру предложений. Вмешивайся и разбивай предложение на несколько частей только в тех случаях, когда оно становится **аномально длинным и громоздким** для чтения из-за обилия придаточных частей или перечислений.
        3.  **Строгое сохранение контента:** Категорически запрещено изменять слова, добавлять что-либо от себя или делать выводы. Твоя работа — это работа редактора-форматировщика, а не копирайтера. Сохрани исходный текст в максимальной близости к оригиналу, изменив только разбивку на абзацы и, в редких случаях, структуру самых длинных предложений.
        Исходный текст для обработки:
        ---
        {text_to_format}
        ---
        """
        response = model.generate_content(prompt)
        formatted_text = response.text
        logging.info("Successfully formatted text with Gemini.")
        return formatted_text
    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}")
        return text_to_format

# --- РАБОТА С TELEGRAM API ---
# Import functions from telegram service for backward compatibility
from services.telegram import (
    send_message,
    edit_message_text,
    send_document,
    get_file_path,
    download_file
)

# --- РАБОТА С OPENAI ---
def transcribe_audio(audio_path): # ИЗМЕНЕНИЕ: используем кортеж с именем файла
    if audio_service:
        return audio_service.transcribe_audio(audio_path)
    # Fallback to legacy implementation
    if not SECRETS_LOADED or not openai_client: return None
    try:
        with open(audio_path, "rb") as audio_file:
            file_tuple = (os.path.basename(audio_path), audio_file)
            transcription = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=file_tuple,
                language="ru",
                response_format="json"
            )
        logging.info("Transcription successful.")
        return transcription.text
    except Exception as e: 
        logging.error(f"Error during transcription: {e}")
    return None

# --- РАБОТА С PUB/SUB ---
def publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, status_message_id=None):
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
        'status_message_id': status_message_id
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
    if firestore_service:
        return firestore_service.get_user(user_id)
    # Fallback to legacy db
    doc_ref = db.collection('users').document(str(user_id))
    doc = doc_ref.get()
    return doc.to_dict() if doc.exists else None
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
    else:
        doc_ref = db.collection('user_states').document(str(user_id))
        if state_data: doc_ref.set(state_data if isinstance(state_data, dict) else {'state': state_data})
        else: doc_ref.delete()
def get_user_state(user_id):
    if firestore_service:
        return firestore_service.get_user_state(user_id)
    doc_ref = db.collection('user_states').document(str(user_id))
    doc = doc_ref.get()
    return doc.to_dict() if doc.exists else None
def get_all_users_for_admin():
    if firestore_service:
        return firestore_service.get_all_users()
    users_list = []
    docs = db.collection('users').stream()
    for doc in docs:
        data = doc.to_dict()
        users_list.append({
            'id': doc.id,
            'name': data.get('first_name', f'ID_{doc.id}'),
            'balance': data.get('balance_minutes', 0)
        })
    return users_list
def remove_user_from_system(user_id):
    if firestore_service:
        firestore_service.delete_user(user_id)
    else:
        db.collection('users').document(str(user_id)).delete()
        db.collection('user_states').document(str(user_id)).delete()
        trial_req_ref = db.collection('trial_requests').document(str(user_id))
        if trial_req_ref.get().exists:
            trial_req_ref.delete()
def log_transcription_attempt(user_id, name, size, duration_secs, status, char_count=0):
    if firestore_service:
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
    elif db:
        try:
            log_ref = db.collection('transcription_logs').document()
            log_ref.set({
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
    if firestore_service:
        try:
            firestore_service.log_payment({
                'user_id': str(user_id),
                'user_name': user_name,
                'telegram_payment_charge_id': telegram_charge_id,
                'provider_payment_charge_id': provider_charge_id,
                'stars_amount': stars_amount,
                'minutes_credited': minutes_credited,
                'package_name': package_name,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            logging.info(f"Logged payment for user {user_id}: {stars_amount} Stars for {minutes_credited} minutes.")
        except Exception as e:
            logging.error(f"Error logging payment for user {user_id}: {e}")
    elif db:
        try:
            log_ref = db.collection('payment_logs').document()
            log_ref.set({
                'user_id': str(user_id),
                'user_name': user_name,
                'telegram_payment_charge_id': telegram_charge_id,
                'provider_payment_charge_id': provider_charge_id,
                'stars_amount': stars_amount,
                'minutes_credited': minutes_credited,
                'package_name': package_name,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            logging.info(f"Logged payment for user {user_id}: {stars_amount} Stars for {minutes_credited} minutes.")
        except Exception as e:
            logging.error(f"Error logging payment for user {user_id}: {e}")
def log_oversized_file(user_id, user_name, file_id, reported_size, file_name=None, mime_type=None):
    if firestore_service:
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
    elif db:
        try:
            log_ref = db.collection('oversized_files_log').document()
            data_to_log = {
                'user_id': str(user_id),
                'user_name': user_name,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'reported_size': reported_size
            }
            if file_id: data_to_log['file_id'] = file_id
            if file_name: data_to_log['file_name'] = file_name
            if mime_type: data_to_log['mime_type'] = mime_type
            log_ref.set(data_to_log)
            logging.info(f"Logged oversized file from user {user_id}, size: {reported_size}")
        except Exception as e:
            logging.error(f"Error logging oversized file for user {user_id}: {e}")

# --- TRIAL REQUESTS HELPERS ---
def create_trial_request(user_id, user_name):
    if firestore_service:
        trial_request = firestore_service.get_trial_request(user_id)
        if not trial_request or trial_request.get('status') == 'denied_can_reapply':
            firestore_service.create_trial_request(user_id, {
                'user_id': str(user_id),
                'user_name': user_name,
                'request_timestamp': firestore.SERVER_TIMESTAMP,
                'status': 'pending'
            })
            return True
        current_status = trial_request.get('status')
        if current_status in ['pending', 'pending_reconsideration']:
            return "already_pending"
        if current_status == 'approved':
            return "already_approved"
        return False
    else:
        doc_ref = db.collection('trial_requests').document(str(user_id))
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get('status') == 'denied_can_reapply':
            doc_ref.set({
                'user_id': str(user_id),
                'user_name': user_name,
                'request_timestamp': firestore.SERVER_TIMESTAMP,
                'status': 'pending'
            })
            return True
        current_status = doc.to_dict().get('status')
        if current_status in ['pending', 'pending_reconsideration']:
            return "already_pending"
        if current_status == 'approved':
            return "already_approved"
        return False
def get_pending_trial_requests():
    if firestore_service:
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
    else:
        requests = []
        docs = db.collection('trial_requests') \
                 .where(filter=FieldFilter('status', '==', 'pending')) \
                 .order_by('request_timestamp', direction=firestore.Query.ASCENDING) \
                 .limit(5) \
                 .stream()
        for doc in docs:
            data = doc.to_dict()
            requests.append({
                'id': doc.id,
                'user_name': data.get('user_name'),
                'user_id_str': data.get('user_id'),
                'timestamp': data.get('request_timestamp')
            })
        return requests
def update_trial_request_status(user_id, status, admin_comment=None, reconsideration_text=None):
    if firestore_service:
        data_to_update = {'status': status}
        if admin_comment: data_to_update['admin_comment'] = admin_comment
        if reconsideration_text: data_to_update['reconsideration_text'] = reconsideration_text
        firestore_service.update_trial_request(user_id, data_to_update)
    else:
        doc_ref = db.collection('trial_requests').document(str(user_id))
        data_to_update = {'status': status, 'last_update_timestamp': firestore.SERVER_TIMESTAMP}
        if admin_comment: data_to_update['admin_comment'] = admin_comment
        if reconsideration_text: data_to_update['reconsideration_text'] = reconsideration_text
        doc_ref.update(data_to_update)

# --- NOTIFICATION HELPER ---
def check_and_notify_pending_trials(force_check=False):
    if not (db or firestore_service) or not OWNER_ID or not SECRETS_LOADED : return
    now = datetime.now(pytz.utc)
    
    if firestore_service:
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
                # For new notifications, we'd need to implement filtered query in service
                # For now, use the get_pending_trial_requests and check manually
                pending_requests = firestore_service.get_pending_trial_requests(limit=1)
                if pending_requests:
                    send_message(OWNER_ID, "🔔 Появились новые заявки на пробный доступ! Для просмотра: /review_trials")
                    firestore_service.update_last_trial_notification_timestamp()
        except Exception as e:
            logging.error(f"Error checking/notifying pending trials: {e}")
    else:
        # Legacy implementation
        state_ref = db.collection('internal_bot_state').document(LAST_TRIAL_NOTIFICATION_TIMESTAMP_DOC_ID)
        state_doc = state_ref.get()
        last_notified_ts = None
        if state_doc.exists:
            last_notified_ts = state_doc.to_dict().get('timestamp')
        if not force_check and last_notified_ts and (now - last_notified_ts).total_seconds() < MIN_NOTIFICATION_INTERVAL_SECONDS:
            return
        try:
            pending_requests_query = db.collection('trial_requests').where(filter=FieldFilter('status', '==', 'pending'))
            if last_notified_ts and not force_check:
                pending_requests_query = pending_requests_query.where(filter=FieldFilter('request_timestamp', '>', last_notified_ts))
            if force_check:
                all_pending_docs = list(db.collection('trial_requests').where(filter=FieldFilter('status', '==', 'pending')).stream())
                count = len(all_pending_docs)
                if count > 0:
                     send_message(OWNER_ID, f"🔔 Ежедневное напоминание: Есть необработанные заявки на пробный доступ ({count} шт.). Для просмотра: /review_trials")
                     state_ref.set({'timestamp': firestore.SERVER_TIMESTAMP, 'daily_check_done': True}, merge=True)
            else:
                pending_docs = list(pending_requests_query.limit(1).stream())
                if pending_docs:
                    send_message(OWNER_ID, "🔔 Появились новые заявки на пробный доступ! Для просмотра: /review_trials")
                    state_ref.set({'timestamp': firestore.SERVER_TIMESTAMP}, merge=True)
        except Exception as e:
            logging.error(f"Error checking/notifying pending trials: {e}")

# --- UTILITY HELPERS ---
def is_authorized(user_id, user_data_from_db): # ... (без изменений)
    if user_id == OWNER_ID: return True
    if user_data_from_db and (user_data_from_db.get('balance_minutes', 0) > 0 or user_data_from_db.get('trial_status') == 'approved'):
        if user_data_from_db.get('trial_status') == 'approved' and user_data_from_db.get('balance_minutes', 0) <= 0: return False
        return True
    return False
# ... (get_first_sentence, get_moscow_time_str, escape_html, get_moscow_time_ranges, format_duration, format_size - без изменений)
def get_first_sentence(text):
    if not text: return ""
    match = re.search(r'^.*?[.!?](?=\s|$)', text, re.DOTALL)
    return match.group(0) if match else text.split('\n')[0]
def get_moscow_time_str():
    moscow_tz = pytz.timezone("Europe/Moscow")
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    now_moscow = now_utc.astimezone(moscow_tz)
    return now_moscow.strftime("%Y-%m-%d_%H-%M-%S")
def escape_html(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
def get_moscow_time_ranges():
    moscow_tz = pytz.timezone("Europe/Moscow")
    now_moscow = datetime.now(moscow_tz)
    utc_tz = pytz.utc
    today_start_msk = now_moscow.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_msk = today_start_msk + timedelta(days=1)
    week_start_msk = today_start_msk - timedelta(days=now_moscow.weekday())
    week_end_msk = week_start_msk + timedelta(days=7)
    month_start_msk = today_start_msk.replace(day=1)
    next_month_calc = month_start_msk.replace(day=28) + timedelta(days=4)
    month_end_msk = next_month_calc.replace(day=1) 
    year_start_msk = today_start_msk.replace(month=1, day=1)
    next_year_calc = year_start_msk.replace(year=year_start_msk.year + 1)
    year_end_msk = next_year_calc
    return {
        "Сегодня": (today_start_msk.astimezone(utc_tz), today_end_msk.astimezone(utc_tz)),
        "Эта неделя": (week_start_msk.astimezone(utc_tz), week_end_msk.astimezone(utc_tz)),
        "Этот месяц": (month_start_msk.astimezone(utc_tz), month_end_msk.astimezone(utc_tz)),
        "Этот год": (year_start_msk.astimezone(utc_tz), year_end_msk.astimezone(utc_tz)),
    }
def get_stats_data(start_utc, end_utc):
    stats = {}
    query = db.collection('transcription_logs') \
              .where(filter=FieldFilter('timestamp', '>=', start_utc)) \
              .where(filter=FieldFilter('timestamp', '<', end_utc))
    docs = query.stream()
    for doc in docs:
        data = doc.to_dict()
        user_id_stat = data.get('user_id')
        if not user_id_stat: continue
        if user_id_stat not in stats:
            stats[user_id_stat] = {'name': data.get('editor_name', f'ID_{user_id_stat}'),'requests': 0,'failures': 0,'duration': 0,'size': 0,'chars': 0}
        stats[user_id_stat]['requests'] += 1
        stats[user_id_stat]['duration'] += data.get('duration', 0)
        stats[user_id_stat]['size'] += data.get('file_size', 0)
        stats[user_id_stat]['chars'] += data.get('char_count', 0)
        if data.get('status') != 'success':
            stats[user_id_stat]['failures'] += 1
    return stats
def format_duration(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
def format_size(bytes_size):
    if bytes_size < 1024: return f"{bytes_size} B"
    elif bytes_size < 1024**2: return f"{bytes_size/1024:.1f} KB"
    elif bytes_size < 1024**3: return f"{bytes_size/1024**2:.1f} MB"
    else: return f"{bytes_size/1024**3:.1f} GB"

def pluralize_russian(number, one, two_four, many):
    """
    Правильное склонение существительных с числительными в русском языке
    number: число
    one: форма для 1 (файл)
    two_four: форма для 2-4 (файла)
    many: форма для 5+ (файлов)
    """
    if number % 10 == 1 and number % 100 != 11:
        return f"{number} {one}"
    elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
        return f"{number} {two_four}"
    else:
        return f"{number} {many}"
def get_average_audio_length_last_30_days(user_id_str):
    if not db: return None
    utc_tz = pytz.utc
    now_utc = datetime.now(utc_tz)
    thirty_days_ago_utc = now_utc - timedelta(days=30)
    logging.info(f"AVG_LEN_LOG: Fetching logs for user {user_id_str} between {thirty_days_ago_utc} and {now_utc}")
    try:
        docs_query = db.collection('transcription_logs') \
                 .where(filter=FieldFilter('user_id', '==', user_id_str)) \
                 .where(filter=FieldFilter('timestamp', '>=', thirty_days_ago_utc)) \
                 .where(filter=FieldFilter('timestamp', '<=', now_utc)) \
                 .where(filter=FieldFilter('status', '==', 'success'))
        docs = docs_query.stream()
        retrieved_doc_timestamps = [] 
        total_duration = 0
        count = 0
        for doc in docs:
            data = doc.to_dict()
            # Use FFmpeg duration if available, otherwise fall back to duration
            doc_duration = data.get('ffmpeg_duration', data.get('duration', 0))
            retrieved_doc_timestamps.append(data.get('timestamp')) 
            total_duration += doc_duration
            count += 1
            logging.info(f"AVG_LEN_LOG: Doc {count}: duration={doc_duration}s ({doc_duration/60:.1f}m), ffmpeg={data.get('ffmpeg_duration')}, telegram={data.get('telegram_duration')}")
        logging.info(f"AVG_LEN_LOG: Found {count} successful logs for user {user_id_str} in last 30 days. Total duration: {total_duration}s, Average: {total_duration/count if count > 0 else 0:.1f}s")
        if count > 0:
            avg_seconds = total_duration / count
            return math.floor(avg_seconds / 60)
    except Exception as e:
        logging.error(f"AVG_LEN_LOG: Error calculating average audio length for {user_id_str}: {e}")
    return None

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
                         keyboard = {"inline_keyboard": [[{"text": "✅ Одобрить", "callback_data": f"approvetrial_{target_user_id_for_denial}_{target_user_name_for_denial}"},{"text": "❌ Отклонить", "callback_data": f"denytrial_{target_user_id_for_denial}_{target_user_name_for_denial}"}]]}
                         edit_message_text(OWNER_ID, admin_original_message_id, f"Заявка от: {target_user_name_for_denial} (ID: {target_user_id_for_denial})", reply_markup=keyboard)
                else:
                    update_trial_request_status(target_user_id_for_denial, "denied_with_comment", admin_comment=admin_comment)
                    reconsider_keyboard = {"inline_keyboard": [[{"text": "Запросить пересмотр", "callback_data": f"reconsider_{target_user_id_for_denial}"}]]}
                    send_message(target_user_id_for_denial, f"К сожалению, в пробном доступе отказано.\nПричина: {admin_comment}", reply_markup=reconsider_keyboard)
                    send_message(OWNER_ID, f"Отказ для {target_user_name_for_denial} с комментарием отправлен.")
                    if admin_original_message_id: edit_message_text(OWNER_ID, admin_original_message_id, f"❌ Отказ для {target_user_name_for_denial} отправлен.")
                return "OK", 200
            
            if user_id != OWNER_ID and current_user_state_doc and current_user_state_doc.get('state') == 'awaiting_reconsideration_text':
                # ... (код без изменений)
                reconsideration_text_from_user = text
                set_user_state(user_id, None)
                update_trial_request_status(user_id, "pending_reconsideration", reconsideration_text=reconsideration_text_from_user)
                trial_request_doc_reconsider = db.collection('trial_requests').document(str(user_id)).get()
                trial_request = trial_request_doc_reconsider.to_dict() if trial_request_doc_reconsider.exists else None
                admin_comment_original = trial_request.get('admin_comment', 'Нет') if trial_request else 'Нет'
                send_message(OWNER_ID, f"❗️Запрос на пересмотр от {user_name} (ID: {user_id}):\n"
                                     f"Причина отказа: {admin_comment_original}\n"
                                     f"Обоснование пользователя: {reconsideration_text_from_user}\n"
                                     f"Для одобрения используйте: /credit {user_id} {TRIAL_MINUTES}")
                send_message(user_id, "Ваш запрос на пересмотр отправлен администратору.")
                return "OK", 200
            
            # --- БЛОК С КОМАНДАМИ ---
            if text == "/help": # ... (код как в предыдущем ответе с правками)
                help_text_user = """<b>Привет!</b> Я ваш бот-помощник для транскрибации аудио в текст с последующим форматированием.

<b>Как пользоваться:</b>
1. Просто перешлите аудиофайл или голосовое сообщение, либо пришлите файлом.
2. Можете отправить несколько файлов сразу - они будут обработаны по очереди.
3. Для работы сервиса вам необходимы минуты на балансе.

<b>Основные команды:</b>
• /start - Начать работу с ботом
• /help - Показать это сообщение
• /trial - Запросить пробный доступ (15 минут)

<b>Управление балансом:</b>
• /balance - Проверить текущий баланс
• /buy_minutes - Пополнить баланс через Telegram Stars

<b>Настройки и статус:</b>
• /settings - Настройки форматирования вывода
• /code_on - Включить вывод с тегами &lt;code&gt;
• /code_off - Выключить теги &lt;code&gt;
• /status - Проверить статус очереди обработки
• /batch (или /queue) - Просмотр пакетов файлов

<b>Технические лимиты:</b>
• <b>Макс. размер файла:</b> 20 МБ
• <b>Форматы:</b> MP3, MP4, M4A, WAV, WEBM, OGG
• <b>Оптимальная длительность:</b> 7-8 минут

Для особых условий и корпоративных клиентов: @nafigator
"""
                if user_id == OWNER_ID:
                    help_text_user += """

━━━━━━━━━━━━━━━━━━━━
<b>🔧 Команды администратора:</b>

<b>Управление пользователями:</b>
• /review_trials - Просмотр заявок на пробный доступ
• /credit &lt;user_id&gt; &lt;минуты&gt; - Начислить минуты пользователю
• /remove_user - Удалить пользователя из системы

<b>Статистика и финансы:</b>
• /stat - Детальная статистика использования
• /cost - Расчет затрат на API за текущий месяц
━━━━━━━━━━━━━━━━━━━━"""
                send_message(chat_id, help_text_user, parse_mode="HTML")
                return "OK", 200

            if text == "/balance": # ... (код как в предыдущем ответе с правками)
                # Always get fresh user data for balance
                fresh_user_data = get_user_data(user_id)
                if fresh_user_data:
                    balance = fresh_user_data.get('balance_minutes', 0)
                    balance_message = f"Ваш текущий баланс: {math.floor(balance)} минут."
                    logging.info(f"Balance command: user {user_id} has {balance} minutes")
                    avg_len_minutes = get_average_audio_length_last_30_days(str(user_id))
                    logging.info(f"Balance command: user {user_id} average length = {avg_len_minutes}")
                    if avg_len_minutes is not None:
                        balance_message += f"\nСредняя длина ваших аудио за последний месяц: {avg_len_minutes} мин."
                    else:
                        balance_message += "\nЗа последний месяц у вас не было успешных распознаваний для расчета средней длины."
                    send_message(chat_id, balance_message)
                else:
                    send_message(chat_id, "Вы еще не зарегистрированы. Пожалуйста, отправьте /start или /trial, чтобы запросить доступ.")
                return "OK", 200

            if text == "/status": # Show queue status
                if firestore_service:
                    queue_count = firestore_service.count_pending_jobs()
                    user_position = firestore_service.get_user_queue_position(user_id)
                    
                    status_msg = "📊 <b>Статус очереди обработки</b>\n\n"
                    status_msg += f"Всего в очереди: {pluralize_russian(queue_count, 'файл', 'файла', 'файлов')}\n"
                    
                    if user_position:
                        status_msg += f"Ваша позиция: #{user_position}\n"
                        estimated_wait = user_position * 20  # ~20 seconds per file
                        if estimated_wait < 60:
                            status_msg += f"Примерное время ожидания: {estimated_wait} сек."
                        else:
                            status_msg += f"Примерное время ожидания: {estimated_wait // 60} мин."
                    else:
                        status_msg += "У вас нет файлов в очереди."
                    
                    send_message(chat_id, status_msg, parse_mode="HTML")
                else:
                    send_message(chat_id, "Информация о очереди недоступна.")
                return "OK", 200
            
            if text == "/batch" or text == "/queue": # Show batch processing status
                batch_state = get_user_state(user_id) or {}
                batch_files = batch_state.get('batch_files', {})
                
                if not batch_files:
                    send_message(chat_id, "У вас нет активных пакетов файлов для обработки.")
                else:
                    batch_msg = "📦 <b>Ваши пакеты файлов:</b>\n\n"
                    total_files = 0
                    total_minutes = 0
                    
                    for group_id, files in batch_files.items():
                        batch_msg += f"<b>Пакет {group_id[-4:]}:</b>\n"
                        for idx, file in enumerate(files, 1):
                            batch_msg += f"  {idx}. {file['file_name']} ({format_duration(file['duration'])})\n"
                        batch_msg += f"  Всего: {pluralize_russian(len(files), 'файл', 'файла', 'файлов')}, ~{pluralize_russian(sum(f['duration_minutes'] for f in files), 'минута', 'минуты', 'минут')}\n\n"
                        total_files += len(files)
                        total_minutes += sum(f['duration_minutes'] for f in files)
                    
                    batch_msg += f"<b>Итого:</b> {pluralize_russian(total_files, 'файл', 'файла', 'файлов')}, ~{pluralize_russian(total_minutes, 'минута', 'минуты', 'минут')}"
                    send_message(chat_id, batch_msg, parse_mode="HTML")
                return "OK", 200
            
            if text == "/settings": # Команда настроек
                logging.info(f"Processing /settings for user {user_id}")
                if not user_data:
                    logging.warning(f"No user_data for {user_id}")
                    send_message(chat_id, "Пожалуйста, сначала отправьте /start для регистрации.")
                    return "OK", 200
                
                # Get current settings
                settings = firestore_service.get_user_settings(user_id) if firestore_service else {'use_code_tags': False}
                use_code_tags = settings.get('use_code_tags', False)
                
                settings_text = "⚙️ Настройки\n\n"
                settings_text += "Форматирование вывода:\n"
                if use_code_tags:
                    settings_text += "✅ Вывод с тегами &lt;code&gt; (моноширинный шрифт)\n\n"
                else:
                    settings_text += "✅ Простой текст (обычный шрифт)\n\n"
                    
                settings_text += "Команды:\n"
                settings_text += "/code_on - включить теги &lt;code&gt;\n"
                settings_text += "/code_off - выключить теги &lt;code&gt;\n"
                
                send_message(chat_id, settings_text, parse_mode="HTML")
                return "OK", 200
            
            if text == "/code_on": # Включить теги code
                if not user_data:
                    send_message(chat_id, "Пожалуйста, сначала отправьте /start для регистрации.")
                    return "OK", 200
                    
                if firestore_service:
                    firestore_service.update_user_setting(user_id, 'use_code_tags', True)
                send_message(chat_id, "✅ Вывод с тегами &lt;code&gt; включен", parse_mode="HTML")
                return "OK", 200
                
            if text == "/code_off": # Выключить теги code
                if not user_data:
                    send_message(chat_id, "Пожалуйста, сначала отправьте /start для регистрации.")
                    return "OK", 200
                    
                if firestore_service:
                    firestore_service.update_user_setting(user_id, 'use_code_tags', False)
                send_message(chat_id, "✅ Простой текст включен")
                return "OK", 200
            
            if text == "/trial": # Новая команда
                if user_data and is_authorized(user_id, user_data): # Проверяем, есть ли уже доступ
                    send_message(chat_id, f"{user_name}, у вас уже есть доступ. Ваш баланс: {math.floor(user_data.get('balance_minutes',0))} минут.")
                else:
                    keyboard = {"inline_keyboard": [[{"text": "Подать заявку на пробный доступ", "callback_data": f"requesttrial_{user_id}_{user_name}"}]]}
                    send_message(chat_id, f"Здравствуйте, {user_name}! Чтобы получить пробный доступ на {TRIAL_MINUTES} минут, пожалуйста, нажмите кнопку ниже.", reply_markup=keyboard)
                return "OK", 200
            
            if text == "/buy_minutes" or text == "/top_up": # ... (код как в предыдущем ответе с правками)
                buttons = []
                micro_purchases_count = user_data.get("micro_package_purchases", 0) if user_data else 0
                for pkg_id, pkg_info in PRODUCT_PACKAGES.items():
                    if pkg_id == "micro_10" and micro_purchases_count >= pkg_info.get("purchase_limit", 3):
                        buttons.append([{"text": f"{pkg_info['title']} (лимит исчерпан)", "callback_data": "noop_limit_reached"}])
                        continue
                    buttons.append([{"text": f"{pkg_info['title']} ({pkg_info['minutes']} мин) - {pkg_info['stars_amount']} звёзд", "callback_data": f"selectpkg_{pkg_id}"}])
                reply_markup = {"inline_keyboard": buttons}
                send_message(chat_id, "Выберите пакет для пополнения баланса:", reply_markup=reply_markup)
                return "OK", 200
            
            if user_id == OWNER_ID: # Остальные админ-команды
                # ... (/review_trials, /credit, /remove_user, /stat как были)
                if text == "/review_trials": 
                    pending_requests = get_pending_trial_requests()
                    if not pending_requests: send_message(OWNER_ID, "Нет новых заявок на пробный доступ."); return "OK", 200
                    send_message(OWNER_ID, "Новые заявки на пробный доступ (макс. 5):")
                    for req in pending_requests:
                        req_user_id_str = req['user_id_str']
                        req_user_name_admin = req['user_name']
                        keyboard = {"inline_keyboard": [[{"text": "✅ Одобрить", "callback_data": f"approvetrial_{req_user_id_str}_{req_user_name_admin}"},{"text": "❌ Отклонить", "callback_data": f"denytrial_{req_user_id_str}_{req_user_name_admin}"}]]}
                        send_message(OWNER_ID, f"Заявка от: {req_user_name_admin} (ID: {req_user_id_str})", reply_markup=keyboard)
                    return "OK", 200
                if text.startswith("/credit"): 
                    parts = text.split()
                    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                        target_user_id_credit = int(parts[1])
                        minutes_to_add = int(parts[2])
                        target_user_data_credit = get_user_data(target_user_id_credit)
                        target_user_name_credit = target_user_data_credit.get('first_name', f"User_{target_user_id_credit}") if target_user_data_credit else f"User_{target_user_id_credit}"
                        create_or_update_user(target_user_id_credit, target_user_name_credit, minutes_to_add)
                        new_balance_credit = (target_user_data_credit.get('balance_minutes', 0) if target_user_data_credit else 0) + minutes_to_add
                        send_message(chat_id, f"✅ Успешно начислено {minutes_to_add} минут пользователю {target_user_name_credit} ({target_user_id_credit}). Новый баланс: {math.floor(new_balance_credit)} мин.")
                        if target_user_id_credit != OWNER_ID : send_message(target_user_id_credit, f"🎉 Ваш баланс пополнен администратором! Текущий баланс: {math.floor(new_balance_credit)} минут.")
                    else: send_message(chat_id, "Ошибка. Используйте формат: /credit <ID_пользователя> <количество_минут>")
                    return "OK", 200
                if text == "/remove_user":
                    all_users = get_all_users_for_admin()
                    if not all_users: send_message(chat_id, "Список пользователей пуст."); return "OK", 200
                    
                    user_list_str = "Выберите номер пользователя для удаления:\n"
                    user_map_remove = {}
                    for i, u_data_remove in enumerate(all_users, 1):
                        user_list_str += f"{i}. {u_data_remove['name']} ({u_data_remove['id']}) - Баланс: {math.floor(u_data_remove['balance'])} мин.\n"
                        user_map_remove[str(i)] = u_data_remove['id'] # <--- ИСПРАВЛЕНИЕ №1: Ключ теперь строка
                    
                    set_user_state(user_id, {'state': 'remove_user', 'map': user_map_remove})
                    send_message(chat_id, user_list_str + "\nОтправьте только номер или любой другой текст для отмены.")
                    return "OK", 200

                if owner_state_doc and owner_state_doc.get('state') == 'remove_user':
                    user_map_to_remove = owner_state_doc.get('map', {})
                    set_user_state(user_id, None)
                    
                    # ИСПРАВЛЕНИЕ №2: Сравниваем строки
                    if text.isdigit() and text in user_map_to_remove:
                        user_to_remove_id_str = user_map_to_remove[text] # Используем text (строку) как ключ
                        all_users_now = get_all_users_for_admin()
                        removed_name = next((u['name'] for u in all_users_now if u['id'] == str(user_to_remove_id_str)), f"ID {user_to_remove_id_str}")
                        remove_user_from_system(user_to_remove_id_str)
                        send_message(chat_id, f"✅ Пользователь {removed_name} ({user_to_remove_id_str}) удален.")
                    else:
                        send_message(chat_id, "Отмена. Неверный номер или не число.")
                    return "OK", 200
                if text == "/cost":
                    # Calculate processing costs
                    try:
                        # Get stats for the current month
                        utc_tz = pytz.utc
                        moscow_tz = pytz.timezone("Europe/Moscow")
                        now_moscow = datetime.now(moscow_tz)
                        month_start_msk = now_moscow.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        month_start_utc = month_start_msk.astimezone(utc_tz)
                        
                        # Count successful transcriptions this month
                        query = db.collection('transcription_logs') \
                                  .where(filter=FieldFilter('timestamp', '>=', month_start_utc)) \
                                  .where(filter=FieldFilter('status', '==', 'success'))
                        
                        docs = list(query.stream())
                        total_minutes = 0
                        total_chars = 0
                        
                        for doc in docs:
                            data = doc.to_dict()
                            duration = data.get('ffmpeg_duration', data.get('duration', 0))
                            total_minutes += duration / 60
                            total_chars += data.get('char_count', 0)
                        
                        # Cost estimates (rough)
                        whisper_cost = total_minutes * 0.006  # $0.006 per minute
                        gemini_cost = (total_chars / 1000) * 0.00025  # Rough estimate for Gemini
                        total_cost = whisper_cost + gemini_cost
                        
                        cost_msg = f"""💰 <b>Расчет затрат за текущий месяц</b>
                        
Обработано: {len(docs)} файлов
Общая длительность: {total_minutes:.1f} минут
Общее количество символов: {total_chars:,}

<b>Приблизительные затраты:</b>
• Whisper API: ${whisper_cost:.2f}
• Gemini API: ${gemini_cost:.2f}
• <b>Итого: ${total_cost:.2f}</b>

<i>Примечание: это приблизительный расчет</i>"""
                        
                        send_message(chat_id, cost_msg, parse_mode="HTML")
                    except Exception as e:
                        logging.error(f"Error calculating costs: {e}")
                        send_message(chat_id, "Ошибка при расчете затрат.")
                    return "OK", 200
                
                if text == "/stat":
                    logging.info(f"OWNER {user_id} initiated /stat command.")
                    send_message(chat_id, "Собираю статистику, подождите...")
                    logging.info("Sent 'Собираю статистику...' message.")
                    ranges = get_moscow_time_ranges()
                    full_report = "📊 <b>Статистика использования бота</b> 📊\n\n"
                    logging.info(f"Generated time ranges: {ranges}")
                    for period_name, (start_range, end_range) in ranges.items():
                        logging.info(f"Processing period: {period_name} from {start_range} to {end_range}")
                        period_stats = get_stats_data(start_range, end_range)
                        logging.info(f"Stats for {period_name}: {period_stats}")
                        full_report += f"--- <b>{period_name}</b> ---\n\n" # Отступ
                        if not period_stats:
                            full_report += "Нет данных за этот период.\n\n"
                            continue
                        for editor_id_stat, data_stat in period_stats.items():
                            full_report += f"  👤 <b>{data_stat['name']}</b> ({editor_id_stat}):\n"
                            full_report += f"     - Запросы: {data_stat['requests']} (Неудач: {data_stat['failures']})\n"
                            full_report += f"     - Общая длительность: {format_duration(data_stat['duration'])}\n"
                            avg_duration_per_request = 0
                            successful_requests = data_stat['requests'] - data_stat['failures']
                            if successful_requests > 0:
                                 avg_duration_per_request = data_stat['duration'] / successful_requests
                            full_report += f"     - Средняя длительность: {format_duration(avg_duration_per_request)}\n"
                            full_report += f"     - Размер: {format_size(data_stat['size'])}\n"
                            full_report += f"     - Знаков: {data_stat['chars']:,}\n\n" # Отступ
                        # full_report += "\n" # Убрали лишний
                    logging.info(f"Final report generated, length: {len(full_report)}. Preview: {full_report[:500]}")
                    if len(full_report) > 4096:
                         logging.info("Report is too long, sending as a file.")
                         send_message(chat_id, "Отчет слишком длинный, отправляю как файл.")
                         temp_txt_path = os.path.join('/tmp', 'stat_report.txt')
                         report_for_file = full_report.replace('<b>','').replace('</b>','').replace('📊','').replace('👤','') # Убираем HTML для txt
                         report_for_file = re.sub(r'--- (.*?) ---\n\n', r'\1\n\n', report_for_file) # Убираем ---
                         with open(temp_txt_path, 'w', encoding='utf-8') as f: f.write(report_for_file)
                         send_document(chat_id, temp_txt_path, caption="Статистика")
                         if os.path.exists(temp_txt_path): os.remove(temp_txt_path)
                    else:
                        logging.info("Sending report as a message.")
                        send_message(chat_id, full_report, parse_mode="HTML") # Используем HTML для статы
                    logging.info("/stat command processing finished.")
                    return "OK", 200

            if text == "/start":
                # ... (код /start как был) ...
                if user_data:
                    balance = user_data.get('balance_minutes', 0)
                    if user_name and (user_data.get('first_name') != user_name or user_data.get('first_name', '').startswith("Manual_")):
                        create_or_update_user(user_id, user_name)
                    send_message(chat_id, f"С возвращением, {user_name}! Ваш баланс: {math.floor(balance)} мин.")
                else:
                    send_message(chat_id, f"Здравствуйте, {user_name}! Пожалуйста, используйте команду /trial, чтобы запросить пробный доступ.")
                return "OK", 200

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
                    oversized_message = f"""⚠️ Файл '<b>{original_file_name or 'Без имени'}</b>' ({format_size(file_size)}) превышает лимит в 20 МБ для автоматической обработки через Telegram.

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
                        
                        batch_indicator = f"📦 Пакет файлов ({len(batch_files[media_group_id])} из нескольких)\n"
                        
                        # For batch files after the first, don't create individual status messages
                        if len(batch_files[media_group_id]) > 1:
                            # Just send a simple confirmation
                            simple_msg = f"📎 Файл добавлен в очередь\n"
                            if original_file_name:
                                simple_msg += f"📄 {original_file_name}\n"
                            simple_msg += f"⏱ {format_duration(duration)}"
                            send_message(chat_id, simple_msg)
                            
                            # Publish job without status message
                            job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, None)
                            if job_id:
                                logging.info(f"Batch audio job {job_id} published for user {user_id}")
                            else:
                                send_message(chat_id, '❌ Ошибка: Не удалось добавить файл в очередь.')
                            return "OK", 200
                    
                    # Create informative initial message
                    file_info_msg = "📎 <b>Файл получен</b>\n\n"
                    if batch_indicator:
                        file_info_msg += batch_indicator
                    if original_file_name:
                        file_info_msg += f"📄 Имя: {original_file_name}\n"
                    file_info_msg += f"⏱ Длительность: {format_duration(duration)}\n"
                    file_info_msg += f"📊 Размер: {format_size(file_size)}\n"
                    file_info_msg += f"💳 Будет списано: {duration_minutes} мин.\n\n"
                    file_info_msg += "⏳ Обрабатываю..."
                    
                    # Send initial status message
                    status_msg = send_message(chat_id, file_info_msg, parse_mode="HTML")
                    status_message_id = status_msg.get('result', {}).get('message_id') if status_msg else None
                    
                    # Publish job to Pub/Sub
                    job_id = publish_audio_job(user_id, chat_id, file_id, file_size, duration, user_name, status_message_id)
                    
                    if job_id:
                        logging.info(f"Audio job {job_id} published for user {user_id}")
                        
                        # Check queue position
                        if firestore_service:
                            queue_count = firestore_service.count_pending_jobs()
                            if queue_count > 1:
                                queue_msg = file_info_msg.replace("⏳ Обрабатываю...", 
                                    f"📊 В очереди: {pluralize_russian(queue_count, 'файл', 'файла', 'файлов')}\n⏳ Обрабатываю...")
                                edit_message_text(chat_id, status_message_id, queue_msg, parse_mode="HTML")
                    else:
                        send_message(chat_id, '❌ Ошибка: Не удалось начать обработку файла.')
                        # Refund the minutes
                        if user_id != OWNER_ID:
                            create_or_update_user(user_id, user_name, duration_minutes)
                        log_transcription_attempt(user_id, user_name, file_size, duration, 'failure_publish')
                    
                    return "OK", 200
                
                # Fallback to synchronous processing
                file_info_msg = "📎 <b>Файл получен</b>\n\n"
                if original_file_name:
                    file_info_msg += f"📄 Имя: {original_file_name}\n"
                file_info_msg += f"⏱ Длительность: {format_duration(duration)}\n"
                file_info_msg += f"📊 Размер: {format_size(file_size)}\n"
                file_info_msg += f"💳 Будет списано: {duration_minutes} мин.\n\n"
                file_info_msg += "⏳ Распознаю и форматирую..."
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

                transcribed_text = transcribe_audio(converted_mp3_path)
                if os.path.exists(converted_mp3_path): os.remove(converted_mp3_path)
                
                if transcribed_text:
                    # Deduct minutes after successful transcription for all users
                    create_or_update_user(user_id, user_name, -duration_minutes)
                    new_balance = balance - duration_minutes
                    if user_id != OWNER_ID:
                        send_message(chat_id, f"Распознавание завершено. Списано {duration_minutes} мин. Остаток: {math.floor(new_balance)} мин.")
                    else:
                        send_message(chat_id, f"Распознавание завершено (админ). Списано {duration_minutes} мин.")
                    
                    formatted_text = format_text_with_gemini(transcribed_text)
                    char_count = len(formatted_text)
                    log_transcription_attempt(user_id, user_name, file_size, duration, 'success', char_count)
                    
                    caption = get_first_sentence(formatted_text)
                    if len(caption) > 1024: caption = caption[:1021] + "..."
                    
                    # Get user settings
                    settings = firestore_service.get_user_settings(user_id) if firestore_service else {'use_code_tags': False}
                    use_code_tags = settings.get('use_code_tags', False)
                    
                    if len(formatted_text) > MAX_MESSAGE_LENGTH:
                        file_name = get_moscow_time_str() + ".txt"
                        temp_txt_path = os.path.join('/tmp', file_name)
                        try:
                            with open(temp_txt_path, 'w', encoding='utf-8') as f: f.write(formatted_text)
                            send_document(chat_id, temp_txt_path, caption=caption)
                            if os.path.exists(temp_txt_path): os.remove(temp_txt_path)
                        except Exception as e:
                             logging.error(f"Error creating/sending txt file: {e}")
                             # Format text based on user preference
                             if use_code_tags:
                                 error_text = f"❌ Ошибка при создании файла, отправляю как текст:\n<code>{escape_html(formatted_text[:MAX_MESSAGE_LENGTH])}...</code>"
                             else:
                                 error_text = f"❌ Ошибка при создании файла, отправляю как текст:\n{formatted_text[:MAX_MESSAGE_LENGTH]}..."
                             send_message(chat_id, error_text, "HTML" if use_code_tags else None)
                             if os.path.exists(temp_txt_path): os.remove(temp_txt_path)
                    else:
                        # Format text based on user preference
                        if use_code_tags:
                            send_message(chat_id, f"<code>{escape_html(formatted_text)}</code>", parse_mode="HTML")
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

# For local testing
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)