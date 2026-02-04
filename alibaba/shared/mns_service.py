"""
MNSService - Alibaba Cloud Message Service adapter for Telegram Whisper Bot
Replaces Google Cloud Pub/Sub for full Alibaba Cloud migration

Usage:
    from telegram_bot_shared.services.mns_service import MNSService

    service = MNSService(
        endpoint='https://account_id.mns.region.aliyuncs.com',
        access_key_id='your-access-key-id',
        access_key_secret='your-access-key-secret',
        queue_name='audio-processing-jobs'
    )
"""

import logging
import json
import base64
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)


class MNSService:
    """
    Service for interacting with Alibaba Cloud MNS (Message Notification Service).
    API-compatible with Pub/Sub usage in the original codebase.
    """

    def __init__(self, endpoint: str, access_key_id: str,
                 access_key_secret: str, queue_name: str):
        """Initialize MNS client."""
        try:
            from mns.account import Account
            from mns.queue import Queue

            self.account = Account(endpoint, access_key_id, access_key_secret)
            self.queue = self.account.get_queue(queue_name)
            self.queue_name = queue_name
            self.endpoint = endpoint

            logger.info(f"MNSService initialized for queue: {queue_name}")

        except ImportError:
            logger.error("aliyun-mns package not installed. Run: pip install aliyun-mns")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize MNSService: {e}")
            raise

    def publish_message(self, message_data: Dict[str, Any],
                        delay_seconds: int = 0) -> Optional[str]:
        """
        Publish a message to the queue.

        Args:
            message_data: Dictionary to serialize and send
            delay_seconds: Delay before message becomes visible (0-604800)

        Returns:
            Message ID if successful, None otherwise
        """
        from mns.mns_exception import MNSExceptionBase

        try:
            from mns.message import Message

            # Serialize message to JSON
            message_body = json.dumps(message_data, default=str)

            # Create MNS message
            msg = Message(message_body)
            if delay_seconds > 0:
                msg.delay_seconds = delay_seconds

            # Send message
            send_msg = self.queue.send_message(msg)

            logger.info(f"Published message {send_msg.message_id} to queue {self.queue_name}")
            return send_msg.message_id

        except MNSExceptionBase as e:
            logger.error(f"MNS error publishing message: {e}")
            return None
        except Exception as e:
            logger.error(f"Error publishing message: {e}")
            return None

    def receive_message(self, wait_seconds: int = 10,
                       visibility_timeout: int = 60) -> Optional[Dict[str, Any]]:
        """
        Receive a message from the queue.

        Args:
            wait_seconds: Long polling wait time (0-30)
            visibility_timeout: Time message is hidden from other consumers

        Returns:
            Dictionary with 'data' and 'receipt_handle' if message received
        """
        from mns.mns_exception import MNSExceptionBase

        try:
            recv_msg = self.queue.receive_message(wait_seconds)

            # Parse message body
            message_data = json.loads(recv_msg.message_body)

            return {
                'data': message_data,
                'message_id': recv_msg.message_id,
                'receipt_handle': recv_msg.receipt_handle,
                'dequeue_count': recv_msg.dequeue_count,
                'enqueue_time': recv_msg.enqueue_time,
            }

        except MNSExceptionBase as e:
            if 'MessageNotExist' in str(e):
                # No messages available (normal condition)
                return None
            logger.error(f"MNS error receiving message: {e}")
            return None
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None

    def delete_message(self, receipt_handle: str) -> bool:
        """
        Delete a message after successful processing.

        Args:
            receipt_handle: Receipt handle from receive_message

        Returns:
            True if deleted successfully
        """
        from mns.mns_exception import MNSExceptionBase

        try:
            self.queue.delete_message(receipt_handle)
            logger.debug(f"Deleted message with handle {receipt_handle[:20]}...")
            return True

        except MNSExceptionBase as e:
            logger.error(f"MNS error deleting message: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return False

    def change_message_visibility(self, receipt_handle: str,
                                   visibility_timeout: int) -> Optional[str]:
        """
        Change message visibility timeout (extend processing time).

        Args:
            receipt_handle: Receipt handle from receive_message
            visibility_timeout: New timeout in seconds

        Returns:
            New receipt handle if successful
        """
        from mns.mns_exception import MNSExceptionBase

        try:
            new_handle = self.queue.change_message_visibility(
                receipt_handle,
                visibility_timeout
            )
            logger.debug(f"Changed visibility for message")
            return new_handle

        except MNSExceptionBase as e:
            logger.error(f"MNS error changing visibility: {e}")
            return None
        except Exception as e:
            logger.error(f"Error changing visibility: {e}")
            return None

    def get_queue_attributes(self) -> Optional[Dict[str, Any]]:
        """
        Get queue attributes (message count, etc.).

        Returns:
            Dictionary with queue attributes
        """
        from mns.mns_exception import MNSExceptionBase

        try:
            attrs = self.queue.get_attributes()
            return {
                'active_messages': attrs.active_messages,
                'inactive_messages': attrs.inactive_messages,
                'delay_messages': attrs.delay_messages,
                'created_time': attrs.create_time,
                'last_modify_time': attrs.last_modify_time,
            }

        except MNSExceptionBase as e:
            logger.error(f"MNS error getting queue attributes: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting queue attributes: {e}")
            return None

    def process_messages(self, handler: Callable[[Dict[str, Any]], bool],
                         max_messages: int = 10,
                         wait_seconds: int = 10) -> int:
        """
        Process messages in a loop.

        Args:
            handler: Function that takes message data and returns True on success
            max_messages: Maximum messages to process before returning
            wait_seconds: Long polling wait time

        Returns:
            Number of messages processed
        """
        processed = 0

        for _ in range(max_messages):
            msg = self.receive_message(wait_seconds=wait_seconds)
            if not msg:
                break

            try:
                success = handler(msg['data'])
                if success:
                    self.delete_message(msg['receipt_handle'])
                    processed += 1
                else:
                    logger.warning(f"Handler returned False for message {msg['message_id']}")
            except Exception as e:
                logger.error(f"Error processing message {msg['message_id']}: {e}")
                # Message will become visible again after timeout

        return processed


class MNSPublisher:
    """
    Simplified publisher for Pub/Sub-style usage.
    Compatible with the existing Pub/Sub publisher pattern.
    """

    def __init__(self, endpoint: str, access_key_id: str,
                 access_key_secret: str):
        """Initialize publisher with credentials."""
        self.endpoint = endpoint
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self._queues: Dict[str, MNSService] = {}

    def topic_path(self, project_id: str, topic_name: str) -> str:
        """
        Get topic path (compatibility method).
        In MNS, we use queue names directly.
        """
        return topic_name

    def publish(self, queue_name: str, message_data: bytes) -> 'PublishFuture':
        """
        Publish message to queue (Pub/Sub-compatible interface).

        Args:
            queue_name: Queue name (or topic path for compatibility)
            message_data: Message bytes (JSON)

        Returns:
            PublishFuture with result() method
        """
        # Get or create queue service
        if queue_name not in self._queues:
            self._queues[queue_name] = MNSService(
                self.endpoint,
                self.access_key_id,
                self.access_key_secret,
                queue_name
            )

        queue = self._queues[queue_name]

        # Parse message data
        try:
            data = json.loads(message_data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {'raw': base64.b64encode(message_data).decode('utf-8')}

        # Publish
        message_id = queue.publish_message(data)

        return PublishFuture(message_id)


class PublishFuture:
    """Future-like object for publish result (Pub/Sub compatibility)."""

    def __init__(self, message_id: Optional[str]):
        self._message_id = message_id

    def result(self, timeout: int = 30) -> str:
        """Get the message ID (or raise exception on failure)."""
        if self._message_id is None:
            raise Exception("Failed to publish message")
        return self._message_id
