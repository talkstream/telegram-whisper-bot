"""
Service initialization module for Telegram Whisper Bot
"""

import os
import logging
from typing import Dict, Any, Optional

from google.cloud import secretmanager
from google.cloud import firestore
from google.cloud import pubsub_v1

# Import services
from telegram_bot_shared.services import telegram as telegram_service
from telegram_bot_shared.services.telegram_async import AsyncTelegramService
from telegram_bot_shared.services.firestore import FirestoreService
from telegram_bot_shared.services.audio import AudioService
from telegram_bot_shared.services.utility import UtilityService
from telegram_bot_shared.services.stats import StatsService
from telegram_bot_shared.services.metrics import MetricsService
from telegram_bot_shared.services.workflow import WorkflowService

# Import handlers
from handlers import CommandRouter

# Import app components
from app.notifications import NotificationService


class ServiceContainer:
    """Container for all initialized services"""
    
    def __init__(self):
        self.telegram_bot_token: Optional[str] = None
        self.telegram_api_url: Optional[str] = None
        self.telegram_file_url: Optional[str] = None
        
        self.db: Optional[firestore.Client] = None
        self.firestore_service: Optional[FirestoreService] = None
        self.audio_service: Optional[AudioService] = None
        self.stats_service: Optional[StatsService] = None
        self.metrics_service: Optional[MetricsService] = None
        self.workflow_service: Optional[WorkflowService] = None
        self.notification_service: Optional[NotificationService] = None
        self.publisher: Optional[pubsub_v1.PublisherClient] = None
        self.command_router: Optional[CommandRouter] = None
        self.async_telegram_service: Optional[AsyncTelegramService] = None
        
        self.initialized = False
        
        # Constants
        self.PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')
        self.DATABASE_ID = 'editorials-robot'
        self.LOCATION = 'europe-west1'
        self.OWNER_ID = 775707
        self.TRIAL_MINUTES = 15
        self.MAX_MESSAGE_LENGTH = 4000
        self.MAX_TELEGRAM_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
        self.AUDIO_PROCESSING_TOPIC = os.environ.get('AUDIO_PROCESSING_TOPIC', 'audio-processing-jobs')
        self.USE_ASYNC_PROCESSING = os.environ.get('USE_ASYNC_PROCESSING', 'true').lower() == 'true'
        
        # Product packages
        self.PRODUCT_PACKAGES = {
            "micro_10": {"title": "Промо-пакет 'Микро'", "description": "10 минут транскрибации", 
                        "payload": "buy_micro_10", "stars_amount": 10, "minutes": 10, "purchase_limit": 3},
            "start_50": {"title": "Пакет 'Старт'", "description": "50 минут транскрибации", 
                        "payload": "buy_start_50", "stars_amount": 75, "minutes": 50},
            "standard_200": {"title": "Пакет 'Стандарт'", "description": "200 минут транскрибации", 
                            "payload": "buy_standard_200", "stars_amount": 270, "minutes": 200},
            "profi_1000": {"title": "Пакет 'Профи'", "description": "1000 минут транскрибации", 
                          "payload": "buy_profi_1000", "stars_amount": 1150, "minutes": 1000},
            "max_8888": {"title": "Пакет 'MAX'", "description": "8888 минут транскрибации", 
                        "payload": "buy_max_8888", "stars_amount": 8800, "minutes": 8888},
        }
    
    def initialize(self) -> bool:
        """Initialize all services"""
        if self.initialized:
            return True
            
        if not self.PROJECT_ID:
            logging.error("FATAL: GCP_PROJECT environment variable or fallback Project ID not set.")
            return False
            
        try:
            # Load secrets
            sm_client = secretmanager.SecretManagerServiceClient()
            
            def get_secret(secret_id):
                name = f"projects/{self.PROJECT_ID}/secrets/{secret_id}/versions/latest"
                response = sm_client.access_secret_version(request={"name": name})
                return response.payload.data.decode("UTF-8").strip()
            
            self.telegram_bot_token = get_secret("telegram-bot-token")
            self.telegram_api_url = f"https://api.telegram.org/bot{self.telegram_bot_token}"
            self.telegram_file_url = f"https://api.telegram.org/file/bot{self.telegram_bot_token}"
            
            # Initialize services
            telegram_service.init_telegram_service(self.telegram_bot_token) # Legacy Sync
            self.async_telegram_service = AsyncTelegramService(self.telegram_bot_token) # Async
            
            self.db = firestore.Client(project=self.PROJECT_ID, database=self.DATABASE_ID)
            self.firestore_service = FirestoreService(self.PROJECT_ID, self.DATABASE_ID)
            self.metrics_service = MetricsService(self.db)
            self.audio_service = AudioService(self.metrics_service)
            self.stats_service = StatsService(self.db)
            
            # Initialize workflow service
            self.workflow_service = WorkflowService(
                self.firestore_service,
                self.async_telegram_service, # Using Async Service
                self.publisher if self.USE_ASYNC_PROCESSING else None,
                self.PROJECT_ID,
                self.AUDIO_PROCESSING_TOPIC,
                self.db,
                self.MAX_TELEGRAM_FILE_SIZE
            )
            
            # Initialize notification service (Sync)
            self.notification_service = NotificationService(
                self.firestore_service, 
                telegram_service._telegram_service,
                self.OWNER_ID
            )
            
            # Initialize Pub/Sub if async processing is enabled
            if self.USE_ASYNC_PROCESSING:
                self.publisher = pubsub_v1.PublisherClient()
            
            # Initialize command router with all services
            services_dict = self._create_services_dict()
            constants_dict = self._create_constants_dict()
            self.command_router = CommandRouter(services_dict, constants_dict)
            
            self.initialized = True
            logging.info("All services initialized successfully")
            return True
            
        except Exception as e:
            logging.error(f"Failed to initialize services: {e}")
            return False
    
    def _create_services_dict(self) -> Dict[str, Any]:
        """Create services dictionary for command handlers"""
        return {
            'telegram_service': self.async_telegram_service, # Replaced with Async Service
            'async_telegram_service': self.async_telegram_service,
            'firestore_service': self.firestore_service,
            'stats_service': self.stats_service,
            'metrics_service': self.metrics_service,
            'workflow_service': self.workflow_service,
            'UtilityService': UtilityService,
            'db': self.db,
            'get_user_data': self.firestore_service.get_user,
            'set_user_state': self.firestore_service.set_user_state,
            'create_trial_request': self._create_trial_request_wrapper(),
            'send_document': self.async_telegram_service.send_document, # Replaced with Async
            'get_pending_trial_requests': self.firestore_service.get_pending_trial_requests,
            'get_all_users_for_admin': self.firestore_service.get_all_users,
        }
    
    def _create_constants_dict(self) -> Dict[str, Any]:
        """Create constants dictionary for command handlers"""
        return {
            'OWNER_ID': self.OWNER_ID,
            'PROJECT_ID': self.PROJECT_ID,
            'PRODUCT_PACKAGES': self.PRODUCT_PACKAGES,
            'TRIAL_MINUTES': self.TRIAL_MINUTES,
        }
    
    def _create_trial_request_wrapper(self):
        """Create wrapper function for trial request creation"""
        def create_trial_request(user_id, user_name):
            # Create trial request data
            from datetime import datetime
            import pytz
            
            request_data = {
                'status': 'pending',
                'user_name': user_name,
                'request_timestamp': datetime.now(pytz.utc),
                'user_id': str(user_id)
            }
            
            # Check if trial request already exists
            existing = self.db.collection('trial_requests').document(str(user_id)).get()
            if existing.exists:
                data = existing.to_dict()
                if data.get('status') == 'pending':
                    return "already_pending"
                elif data.get('status') == 'approved':
                    return "already_approved"
            
            # Check if user already has trial
            user_data = self.firestore_service.get_user(user_id)
            if user_data and user_data.get('trial_status') == 'approved':
                return "already_approved"
            
            # Create the trial request
            self.firestore_service.create_trial_request(user_id, request_data)
            
            # Queue notification
            self.notification_service.queue_trial_notification(user_id, user_name, 'new')
            
            return True
        return create_trial_request
    
    def warmup(self) -> float:
        """Perform warmup operations and return elapsed time"""
        import time
        start_time = time.time()
        
        if not self.initialized:
            self.initialize()
        
        # Warm up Firestore connection
        if self.db:
            try:
                # Simple read operation to warm up the connection
                self.db.collection('users').document('warmup_check').get()
                logging.info("Firestore connection warmed up")
            except Exception as e:
                logging.warning(f"Firestore warmup failed: {e}")
        
        elapsed = time.time() - start_time
        logging.info(f"Warmup completed in {elapsed:.2f} seconds")
        return elapsed


# Global service container instance
services = ServiceContainer()