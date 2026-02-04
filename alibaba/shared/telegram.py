import os
import json
import logging
import tempfile
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TelegramService:
    """Service for interacting with Telegram Bot API"""
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self.file_url = f"https://api.telegram.org/file/bot{bot_token}"
        
        # Configure connection pooling
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,    # cached connections
            pool_maxsize=20,        # max connections in pool
            max_retries=3           # retry on connection errors
        )
        self.session.mount('https://', adapter)
        
    def send_chat_action(self, chat_id: int, action: str) -> bool:
        """
        Send a chat action (e.g. 'typing', 'upload_photo', 'upload_document').
        This is fire-and-forget (returns True if request sent, doesn't raise error on failure usually).
        """
        url = f"{self.api_url}/sendChatAction"
        payload = {"chat_id": chat_id, "action": action}
        
        try:
            # Short timeout for chat action as it's not critical
            self.session.post(url, json=payload, timeout=2)
            return True
        except Exception as e:
            logger.warning(f"Failed to send chat action: {e}")
            return False

    def send_message(self, chat_id: int, text: str, parse_mode: str = "", 
                    reply_markup: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Send a message to a Telegram chat"""
        url = f"{self.api_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
            
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            logger.info(f"Sent message to {chat_id}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending message: {e} - Payload: {payload}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
            
    def edit_message_text(self, chat_id: int, message_id: int, text: str, 
                         parse_mode: str = "", reply_markup: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """Edit an existing message"""
        url = f"{self.api_url}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
            
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error editing message: {e}")
            return None
            
    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message"""
        url = f"{self.api_url}/deleteMessage"
        payload = {"chat_id": chat_id, "message_id": message_id}
        
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error deleting message: {e}")
            return False
            
    def send_document(self, chat_id: int, file_path: str, caption: str = "") -> Optional[Dict[str, Any]]:
        """Send a document to a Telegram chat"""
        url = f"{self.api_url}/sendDocument"
        
        try:
            with open(file_path, 'rb') as f:
                files = {'document': (os.path.basename(file_path), f, 'text/plain')}
                data = {'chat_id': str(chat_id)}
                if caption:
                    data['caption'] = caption
                    
                response = self.session.post(url, files=files, data=data)
                response.raise_for_status()
                logger.info(f"Sent document to {chat_id}")
                return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending document: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error preparing/sending document: {e}")
            return None
            
    def get_file_path(self, file_id: str) -> Optional[str]:
        """Get file path from Telegram servers"""
        url = f"{self.api_url}/getFile"
        
        try:
            response = self.session.get(url, params={"file_id": file_id})
            response.raise_for_status()
            data = response.json()
            
            if data.get("ok"):
                return data["result"]["file_path"]
            else:
                logger.error(f"Telegram API error getting file path: {data}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting file path: {e}")
            return None
            
    def download_file(self, file_path: str, target_dir: str = '/tmp') -> Optional[str]:
        """Download file from Telegram servers"""
        url = f"{self.file_url}/{file_path}"
        
        try:
            response = self.session.get(url, stream=True)
            response.raise_for_status()
            
            suffix = os.path.splitext(file_path)[1] or '.ogg'
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=target_dir)
            
            with temp_file as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            logger.info(f"Downloaded file to {temp_file.name}")
            return temp_file.name
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading file: {e}")
            return None
            
    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> bool:
        """Answer a callback query (acknowledge button press)"""
        url = f"{self.api_url}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}

        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error answering callback query: {e}")
            return False

    def answer_pre_checkout_query(self, query_id: str, ok: bool = True, error_message: str = "") -> bool:
        """Answer a pre-checkout query"""
        url = f"{self.api_url}/answerPreCheckoutQuery"
        payload = {"pre_checkout_query_id": query_id, "ok": ok}
        
        if not ok and error_message:
            payload["error_message"] = error_message
            
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            logger.info(f"Answered pre_checkout_query {query_id} with ok={ok}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error answering pre_checkout_query: {e}")
            return False
            
    def send_invoice(self, chat_id: int, title: str, description: str, payload: str,
                    currency: str, prices: list, **kwargs) -> Optional[Dict[str, Any]]:
        """Send an invoice for Telegram Stars payment"""
        url = f"{self.api_url}/sendInvoice"
        
        invoice_params = {
            "chat_id": chat_id,
            "title": title,
            "description": description,
            "payload": payload,
            "currency": currency,
            "prices": prices
        }
        invoice_params.update(kwargs)  # Add any additional parameters
        
        try:
            response = self.session.post(url, json=invoice_params)
            response.raise_for_status()
            logger.info(f"Invoice sent to {chat_id}")
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending invoice: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
            
    def close(self):
        """Close the session"""
        self.session.close()
    
    def format_progress_bar(self, percentage: float, width: int = 20) -> str:
        """Format a progress bar with Unicode characters
        
        Args:
            percentage: Progress percentage (0-100)
            width: Width of the progress bar in characters
            
        Returns:
            Formatted progress bar string like: [████████████░░░░░░░░] 60%
        """
        if percentage < 0:
            percentage = 0
        elif percentage > 100:
            percentage = 100
            
        filled = int(width * percentage / 100)
        empty = width - filled
        
        bar = "▓" * filled + "░" * empty
        return f"[{bar}] {percentage:.0f}%"
    
    def format_time_estimate(self, elapsed_seconds: float, total_seconds: float) -> str:
        """Format time estimate based on elapsed and total time
        
        Args:
            elapsed_seconds: Time elapsed so far
            total_seconds: Total estimated time
            
        Returns:
            Formatted string like: "~2:30 remaining"
        """
        if total_seconds <= 0 or elapsed_seconds < 0:
            return ""
            
        remaining_seconds = max(0, total_seconds - elapsed_seconds)
        
        if remaining_seconds < 60:
            return f"~{int(remaining_seconds)} сек. осталось"
        elif remaining_seconds < 3600:
            minutes = int(remaining_seconds / 60)
            seconds = int(remaining_seconds % 60)
            return f"~{minutes}:{seconds:02d} осталось"
        else:
            hours = int(remaining_seconds / 3600)
            minutes = int((remaining_seconds % 3600) / 60)
            return f"~{hours}ч {minutes}м осталось"
    
    def send_progress_update(self, chat_id: int, message_id: int, stage: str, 
                           percentage: float, time_estimate: str = "") -> Optional[Dict[str, Any]]:
        """Send a formatted progress update
        
        Args:
            chat_id: Chat ID
            message_id: Message ID to edit
            stage: Current stage description
            percentage: Progress percentage
            time_estimate: Optional time estimate
            
        Returns:
            API response or None
        """
        progress_bar = self.format_progress_bar(percentage)
        text = f"⏳ {stage}\n{progress_bar}"
        
        if time_estimate:
            text += f"\n{time_estimate}"
            
        return self.edit_message_text(chat_id, message_id, text)


# Global instance for backward compatibility
_telegram_service: Optional[TelegramService] = None


def init_telegram_service(bot_token: str) -> TelegramService:
    """Initialize the global Telegram service"""
    global _telegram_service
    _telegram_service = TelegramService(bot_token)
    return _telegram_service


def get_telegram_service() -> Optional[TelegramService]:
    """Get the global Telegram service instance"""
    return _telegram_service


# Backward compatibility functions
def send_message(chat_id, text, parse_mode="", reply_markup=None):
    """Legacy wrapper for send_message"""
    if _telegram_service:
        return _telegram_service.send_message(chat_id, text, parse_mode, reply_markup)
    return None


def edit_message_text(chat_id, message_id, text, parse_mode="", reply_markup=None):
    """Legacy wrapper for edit_message_text"""
    if _telegram_service:
        return _telegram_service.edit_message_text(chat_id, message_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    return None


def send_document(chat_id, file_path, caption=""):
    """Legacy wrapper for send_document"""
    if _telegram_service:
        return _telegram_service.send_document(chat_id, file_path, caption)
    return None


def get_file_path(file_id):
    """Legacy wrapper for get_file_path"""
    if _telegram_service:
        return _telegram_service.get_file_path(file_id)
    return None


def download_file(file_path):
    """Legacy wrapper for download_file"""
    if _telegram_service:
        return _telegram_service.download_file(file_path)
    return None