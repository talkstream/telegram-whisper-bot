# Shared services for telegram-whisper-bot
# This directory is copied to webhook-handler/services and audio-processor/services at deploy time

from .tablestore_service import TablestoreService
from .telegram import TelegramService
from .audio import AudioService
from .mns_service import MNSPublisher, MNSService
from .utility import UtilityService

__all__ = [
    'TablestoreService',
    'TelegramService',
    'AudioService',
    'MNSPublisher',
    'MNSService',
    'UtilityService',
]
