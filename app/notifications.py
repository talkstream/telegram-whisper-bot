"""
Notification management service for Telegram Whisper Bot
"""

import logging
from datetime import datetime, timedelta
import pytz
from typing import Dict, List, Optional

from telegram_bot_shared.services.firestore import FirestoreService
from telegram_bot_shared.services.telegram import TelegramService


class NotificationService:
    """Handles all notification logic including trial requests and payment notifications"""
    
    def __init__(self, firestore_service: FirestoreService, telegram_service: TelegramService, owner_id: int):
        self.firestore = firestore_service
        self.telegram = telegram_service
        self.owner_id = owner_id
        
        # Constants
        self.LAST_TRIAL_NOTIFICATION_TIMESTAMP_DOC_ID = "last_trial_notification_ts"
        self.MIN_NOTIFICATION_INTERVAL_SECONDS = 1800  # 30 minutes
        
        # In-memory storage for payment notifications
        self.pending_payment_notifications = []
        self.last_payment_notification_time = None
        
    def check_and_notify_trial_requests(self, send_message_fn=None, force_check=False) -> None:
        """Check for pending trial requests and notify owner"""
        # Check if we've already notified recently
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        last_notified_ts = self.firestore.get_last_trial_notification_timestamp()
        
        if not force_check and last_notified_ts and (now - last_notified_ts).total_seconds() < self.MIN_NOTIFICATION_INTERVAL_SECONDS:
            return
        
        trial_requests = self.firestore.get_pending_trial_requests()
        if trial_requests:
            message = f"📝 Есть {len(trial_requests)} заявок на пробный доступ.\n"
            message += "Используйте /review_trials для просмотра."
            self.telegram.send_message(self.owner_id, message)
            
            if force_check:
                self.firestore.update_last_trial_notification_timestamp(daily_check=True)
            else:
                self.firestore.update_last_trial_notification_timestamp()
            
            logging.info(f"Notified owner about {len(trial_requests)} trial requests")
    
    def queue_payment_notification(self, user_id: int, user_name: str, stars_amount: int, 
                                 minutes_credited: float, package_name: str) -> None:
        """Queue payment notification for batched sending to owner"""
        payment_info = {
            'user_id': user_id,
            'user_name': user_name,
            'stars_amount': stars_amount,
            'minutes_credited': minutes_credited,
            'package_name': package_name,
            'timestamp': datetime.now(pytz.timezone('Europe/Moscow'))
        }
        
        self.pending_payment_notifications.append(payment_info)
        
        # If this is the first notification or it's been more than 10 minutes since last notification
        current_time = datetime.now()
        if (self.last_payment_notification_time is None or 
            (current_time - self.last_payment_notification_time).total_seconds() > 600):
            self._send_batched_payment_notifications()
    
    def _send_batched_payment_notifications(self) -> None:
        """Send all pending payment notifications as a single message"""
        if not self.pending_payment_notifications:
            return
        
        # Build the notification message
        message = "💰 <b>Новые платежи:</b>\n\n"
        total_stars = 0
        total_minutes = 0
        
        for payment in self.pending_payment_notifications:
            message += f"👤 <b>{payment['user_name']}</b> (ID: {payment['user_id']})\n"
            message += f"📦 {payment['package_name']}\n"
            message += f"💫 {payment['stars_amount']} ⭐ → {payment['minutes_credited']:.0f} минут\n"
            message += f"🕐 {payment['timestamp'].strftime('%H:%M')}\n\n"
            
            total_stars += payment['stars_amount']
            total_minutes += payment['minutes_credited']
        
        # Add summary if multiple payments
        if len(self.pending_payment_notifications) > 1:
            message += f"<b>Итого:</b> {total_stars} ⭐ за {total_minutes:.0f} минут"
        
        # Send the notification
        try:
            self.telegram.send_message(self.owner_id, message, parse_mode="HTML")
            logging.info(f"Sent batched payment notification for {len(self.pending_payment_notifications)} payments")
            
            # Clear the queue and update last notification time
            self.pending_payment_notifications.clear()
            self.last_payment_notification_time = datetime.now()
        except Exception as e:
            logging.error(f"Failed to send payment notification: {e}")
    
    def queue_trial_notification(self, user_id: int, user_name: str, notification_type: str = 'new') -> None:
        """Queue trial request notification for owner"""
        # For trial requests, we check and notify immediately if needed
        self.check_and_notify_trial_requests()