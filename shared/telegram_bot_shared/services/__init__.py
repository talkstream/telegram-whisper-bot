"""
Services module for Telegram Whisper Bot
Supports both GCP (Firestore, Pub/Sub) and Alibaba Cloud (Tablestore, MNS)
"""
from .telegram import TelegramService, init_telegram_service
from .firestore import FirestoreService
from .audio import AudioService
from .utility import UtilityService
from .stats import StatsService
from .metrics import MetricsService
from .workflow import WorkflowService

# Alibaba Cloud services (optional imports)
try:
    from .tablestore_service import TablestoreService
except ImportError:
    TablestoreService = None  # type: ignore

try:
    from .mns_service import MNSService, MNSPublisher
except ImportError:
    MNSService = None  # type: ignore
    MNSPublisher = None  # type: ignore

__all__ = [
    # Core services
    'TelegramService', 'init_telegram_service',
    'AudioService', 'UtilityService', 'StatsService',
    'MetricsService', 'WorkflowService',
    # GCP services
    'FirestoreService',
    # Alibaba Cloud services
    'TablestoreService', 'MNSService', 'MNSPublisher',
]