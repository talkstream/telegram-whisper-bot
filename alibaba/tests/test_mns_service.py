#!/usr/bin/env python3
"""
Unit tests for MNSService, MNSPublisher, PublishFuture (alibaba/shared/mns_service.py).

Covers:
- MNSService initialization (success, ImportError)
- publish_message (success, delay, MNS exception, generic exception, Message import fallback)
- receive_message (success, no messages, MNS error, JSON parse error)
- delete_message (success, MNS failure, generic failure)
- change_message_visibility (success, MNS failure, generic failure)
- get_queue_attributes (success, MNS failure, generic failure)
- process_messages (handler success, handler False, handler exception, no messages)
- MNSPublisher (init, topic_path, publish success, publish with cached queue, raw bytes fallback)
- PublishFuture (result success, result failure)

Run: cd alibaba && python -m pytest tests/test_mns_service.py -v
"""
import os
import sys
import json
import base64
from unittest.mock import patch, MagicMock

import pytest


# Sentinel exception class used to simulate MNSExceptionBase
class FakeMNSException(Exception):
    """Fake MNS exception for testing."""
    pass


# Fake Message class that stores constructor arg as message_body
class FakeMessage:
    """Fake MNS Message for testing."""
    def __init__(self, body):
        self.message_body = body
        self.delay_seconds = 0


# Mock MNS module hierarchy before importing the service
mock_mns = MagicMock()
mock_mns_account = MagicMock()
mock_mns_queue = MagicMock()
mock_mns_exception = MagicMock()
mock_mns_exception.MNSExceptionBase = FakeMNSException
mock_mns_common = MagicMock()
mock_mns_common.Message = FakeMessage
mock_mns_message = MagicMock()
mock_mns_message.Message = FakeMessage

sys.modules['mns'] = mock_mns
sys.modules['mns.account'] = mock_mns_account
sys.modules['mns.queue'] = mock_mns_queue
sys.modules['mns.mns_exception'] = mock_mns_exception
sys.modules['mns.mns_common'] = mock_mns_common
sys.modules['mns.message'] = mock_mns_message

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

from mns_service import MNSService, MNSPublisher, PublishFuture


# ─────────────────────────────────────────────────
# MNSService — initialization
# ─────────────────────────────────────────────────

class TestMNSServiceInit:
    """Tests for MNSService.__init__."""

    def test_init_success(self):
        """MNSService creates account and queue on init."""
        mock_account_cls = MagicMock()
        mock_account_instance = MagicMock()
        mock_queue_instance = MagicMock()
        mock_account_cls.return_value = mock_account_instance
        mock_account_instance.get_queue.return_value = mock_queue_instance

        with patch.dict('sys.modules', {'mns.account': MagicMock(Account=mock_account_cls)}):
            svc = MNSService(
                endpoint='https://test.mns.aliyuncs.com',
                access_key_id='key-id',
                access_key_secret='key-secret',
                queue_name='test-queue'
            )

        mock_account_cls.assert_called_once_with(
            'https://test.mns.aliyuncs.com', 'key-id', 'key-secret'
        )
        mock_account_instance.get_queue.assert_called_once_with('test-queue')
        assert svc.queue_name == 'test-queue'
        assert svc.endpoint == 'https://test.mns.aliyuncs.com'

    def test_init_import_error(self):
        """MNSService raises ImportError when mns package missing."""
        with patch.dict('sys.modules', {'mns.account': None}):
            with pytest.raises(ImportError):
                MNSService(
                    endpoint='https://test.mns.aliyuncs.com',
                    access_key_id='key',
                    access_key_secret='secret',
                    queue_name='q'
                )


# ─────────────────────────────────────────────────
# MNSService — publish_message
# ─────────────────────────────────────────────────

class TestPublishMessage:
    """Tests for MNSService.publish_message."""

    def _make_service(self):
        """Create an MNSService with mocked internals."""
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_publish_success(self):
        """publish_message returns message_id on success."""
        svc = self._make_service()
        mock_send_msg = MagicMock()
        mock_send_msg.message_id = 'msg-123'
        svc.queue.send_message.return_value = mock_send_msg

        result = svc.publish_message({'job_id': '42', 'action': 'transcribe'})

        assert result == 'msg-123'
        svc.queue.send_message.assert_called_once()
        # Verify JSON body
        sent_msg = svc.queue.send_message.call_args[0][0]
        body = json.loads(sent_msg.message_body)
        assert body == {'job_id': '42', 'action': 'transcribe'}

    def test_publish_with_delay(self):
        """publish_message sets delay_seconds on the MNS message when > 0."""
        svc = self._make_service()
        mock_send_msg = MagicMock()
        mock_send_msg.message_id = 'msg-delayed'
        svc.queue.send_message.return_value = mock_send_msg

        result = svc.publish_message({'test': True}, delay_seconds=30)

        assert result == 'msg-delayed'
        sent_msg = svc.queue.send_message.call_args[0][0]
        assert sent_msg.delay_seconds == 30

    def test_publish_no_delay_by_default(self):
        """publish_message does NOT set delay_seconds when delay is 0."""
        svc = self._make_service()
        mock_send_msg = MagicMock()
        mock_send_msg.message_id = 'msg-no-delay'
        svc.queue.send_message.return_value = mock_send_msg

        svc.publish_message({'test': True})

        sent_msg = svc.queue.send_message.call_args[0][0]
        # Default FakeMessage has delay_seconds=0, and code should not alter it
        assert sent_msg.delay_seconds == 0

    def test_publish_mns_exception(self):
        """publish_message re-raises MNSExceptionBase."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.send_message.side_effect = FakeMNSException("Queue not found")
            with pytest.raises(FakeMNSException, match="Queue not found"):
                svc.publish_message({'data': 1})

    def test_publish_generic_exception(self):
        """publish_message re-raises generic exceptions."""
        svc = self._make_service()
        svc.queue.send_message.side_effect = RuntimeError("Connection refused")

        with pytest.raises(RuntimeError, match="Connection refused"):
            svc.publish_message({'data': 1})

    def test_publish_serializes_non_string_values(self):
        """publish_message uses default=str for non-serializable types."""
        svc = self._make_service()
        mock_send_msg = MagicMock()
        mock_send_msg.message_id = 'msg-ser'
        svc.queue.send_message.return_value = mock_send_msg

        from datetime import datetime
        dt = datetime(2026, 1, 15, 12, 0, 0)
        result = svc.publish_message({'ts': dt})

        assert result == 'msg-ser'
        sent_msg = svc.queue.send_message.call_args[0][0]
        body = json.loads(sent_msg.message_body)
        assert body['ts'] == str(dt)


# ─────────────────────────────────────────────────
# MNSService — receive_message
# ─────────────────────────────────────────────────

class TestReceiveMessage:
    """Tests for MNSService.receive_message."""

    def _make_service(self):
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_receive_success(self):
        """receive_message returns parsed data with metadata."""
        svc = self._make_service()
        mock_msg = MagicMock()
        mock_msg.message_body = json.dumps({'job_id': '77'})
        mock_msg.message_id = 'msg-77'
        mock_msg.receipt_handle = 'rh-77'
        mock_msg.dequeue_count = 1
        mock_msg.enqueue_time = 1700000000
        svc.queue.receive_message.return_value = mock_msg

        result = svc.receive_message(wait_seconds=5)

        assert result is not None
        assert result['data'] == {'job_id': '77'}
        assert result['message_id'] == 'msg-77'
        assert result['receipt_handle'] == 'rh-77'
        assert result['dequeue_count'] == 1
        assert result['enqueue_time'] == 1700000000
        svc.queue.receive_message.assert_called_once_with(5)

    def test_receive_no_messages(self):
        """receive_message returns None when MessageNotExist."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.receive_message.side_effect = FakeMNSException("MessageNotExist")
            result = svc.receive_message()

        assert result is None

    def test_receive_mns_error(self):
        """receive_message returns None on non-MessageNotExist MNS error."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.receive_message.side_effect = FakeMNSException("InternalError")
            result = svc.receive_message()

        assert result is None

    def test_receive_json_parse_error(self):
        """receive_message returns None on malformed JSON body."""
        svc = self._make_service()
        mock_msg = MagicMock()
        mock_msg.message_body = 'not-valid-json{{{{'
        svc.queue.receive_message.return_value = mock_msg

        result = svc.receive_message()

        assert result is None


# ─────────────────────────────────────────────────
# MNSService — delete_message
# ─────────────────────────────────────────────────

class TestDeleteMessage:
    """Tests for MNSService.delete_message."""

    def _make_service(self):
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_delete_success(self):
        """delete_message returns True on success."""
        svc = self._make_service()

        result = svc.delete_message('receipt-handle-abc')

        assert result is True
        svc.queue.delete_message.assert_called_once_with('receipt-handle-abc')

    def test_delete_mns_failure(self):
        """delete_message returns False on MNS exception."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.delete_message.side_effect = FakeMNSException("ReceiptHandleError")
            result = svc.delete_message('bad-handle')

        assert result is False

    def test_delete_generic_failure(self):
        """delete_message returns False on generic exception."""
        svc = self._make_service()
        svc.queue.delete_message.side_effect = RuntimeError("timeout")

        result = svc.delete_message('handle')

        assert result is False


# ─────────────────────────────────────────────────
# MNSService — change_message_visibility
# ─────────────────────────────────────────────────

class TestChangeMessageVisibility:
    """Tests for MNSService.change_message_visibility."""

    def _make_service(self):
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_change_visibility_success(self):
        """change_message_visibility returns new receipt handle."""
        svc = self._make_service()
        svc.queue.change_message_visibility.return_value = 'new-handle-xyz'

        result = svc.change_message_visibility('old-handle', 120)

        assert result == 'new-handle-xyz'
        svc.queue.change_message_visibility.assert_called_once_with('old-handle', 120)

    def test_change_visibility_mns_failure(self):
        """change_message_visibility returns None on MNS error."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.change_message_visibility.side_effect = FakeMNSException("err")
            result = svc.change_message_visibility('handle', 60)

        assert result is None

    def test_change_visibility_generic_failure(self):
        """change_message_visibility returns None on generic error."""
        svc = self._make_service()
        svc.queue.change_message_visibility.side_effect = OSError("network")

        result = svc.change_message_visibility('handle', 60)

        assert result is None


# ─────────────────────────────────────────────────
# MNSService — get_queue_attributes
# ─────────────────────────────────────────────────

class TestGetQueueAttributes:
    """Tests for MNSService.get_queue_attributes."""

    def _make_service(self):
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_get_attributes_success(self):
        """get_queue_attributes returns parsed attributes dict."""
        svc = self._make_service()
        mock_attrs = MagicMock()
        mock_attrs.active_messages = 5
        mock_attrs.inactive_messages = 2
        mock_attrs.delay_messages = 1
        mock_attrs.create_time = 1700000000
        mock_attrs.last_modify_time = 1700001000
        svc.queue.get_attributes.return_value = mock_attrs

        result = svc.get_queue_attributes()

        assert result == {
            'active_messages': 5,
            'inactive_messages': 2,
            'delay_messages': 1,
            'created_time': 1700000000,
            'last_modify_time': 1700001000,
        }

    def test_get_attributes_mns_failure(self):
        """get_queue_attributes returns None on MNS error."""
        svc = self._make_service()

        with patch.dict('sys.modules', {
            'mns.mns_exception': MagicMock(MNSExceptionBase=FakeMNSException)
        }):
            svc.queue.get_attributes.side_effect = FakeMNSException("access denied")
            result = svc.get_queue_attributes()

        assert result is None

    def test_get_attributes_generic_failure(self):
        """get_queue_attributes returns None on generic error."""
        svc = self._make_service()
        svc.queue.get_attributes.side_effect = ConnectionError("timeout")

        result = svc.get_queue_attributes()

        assert result is None


# ─────────────────────────────────────────────────
# MNSService — process_messages
# ─────────────────────────────────────────────────

class TestProcessMessages:
    """Tests for MNSService.process_messages."""

    def _make_service(self):
        svc = MNSService.__new__(MNSService)
        svc.account = MagicMock()
        svc.queue = MagicMock()
        svc.queue_name = 'test-queue'
        svc.endpoint = 'https://test.mns.aliyuncs.com'
        return svc

    def test_process_handler_success(self):
        """process_messages deletes message and increments count on success."""
        svc = self._make_service()
        msg1 = {
            'data': {'job': 'a'},
            'message_id': 'msg-1',
            'receipt_handle': 'rh-1',
            'dequeue_count': 1,
            'enqueue_time': 0,
        }
        msg2 = {
            'data': {'job': 'b'},
            'message_id': 'msg-2',
            'receipt_handle': 'rh-2',
            'dequeue_count': 1,
            'enqueue_time': 0,
        }

        with patch.object(svc, 'receive_message', side_effect=[msg1, msg2, None]):
            with patch.object(svc, 'delete_message', return_value=True) as mock_del:
                handler = MagicMock(return_value=True)
                processed = svc.process_messages(handler, max_messages=5)

        assert processed == 2
        handler.assert_any_call({'job': 'a'})
        handler.assert_any_call({'job': 'b'})
        assert mock_del.call_count == 2

    def test_process_handler_returns_false(self):
        """process_messages does NOT delete message when handler returns False."""
        svc = self._make_service()
        msg = {
            'data': {'job': 'fail'},
            'message_id': 'msg-f',
            'receipt_handle': 'rh-f',
            'dequeue_count': 1,
            'enqueue_time': 0,
        }

        with patch.object(svc, 'receive_message', side_effect=[msg, None]):
            with patch.object(svc, 'delete_message') as mock_del:
                handler = MagicMock(return_value=False)
                processed = svc.process_messages(handler, max_messages=5)

        assert processed == 0
        mock_del.assert_not_called()

    def test_process_handler_exception(self):
        """process_messages catches handler exceptions, message stays in queue."""
        svc = self._make_service()
        msg = {
            'data': {'job': 'boom'},
            'message_id': 'msg-x',
            'receipt_handle': 'rh-x',
            'dequeue_count': 1,
            'enqueue_time': 0,
        }

        with patch.object(svc, 'receive_message', side_effect=[msg, None]):
            with patch.object(svc, 'delete_message') as mock_del:
                handler = MagicMock(side_effect=ValueError("handler crash"))
                processed = svc.process_messages(handler, max_messages=5)

        assert processed == 0
        mock_del.assert_not_called()

    def test_process_no_messages(self):
        """process_messages returns 0 when no messages available."""
        svc = self._make_service()

        with patch.object(svc, 'receive_message', return_value=None):
            handler = MagicMock()
            processed = svc.process_messages(handler, max_messages=3)

        assert processed == 0
        handler.assert_not_called()

    def test_process_respects_max_messages(self):
        """process_messages stops after max_messages iterations."""
        svc = self._make_service()

        def make_msg(i):
            return {
                'data': {'i': i},
                'message_id': f'msg-{i}',
                'receipt_handle': f'rh-{i}',
                'dequeue_count': 1,
                'enqueue_time': 0,
            }

        # Return messages for every receive call
        with patch.object(svc, 'receive_message', side_effect=[make_msg(i) for i in range(3)]):
            with patch.object(svc, 'delete_message', return_value=True):
                handler = MagicMock(return_value=True)
                processed = svc.process_messages(handler, max_messages=3)

        assert processed == 3
        assert handler.call_count == 3


# ─────────────────────────────────────────────────
# MNSPublisher
# ─────────────────────────────────────────────────

class TestMNSPublisher:
    """Tests for MNSPublisher."""

    def test_init(self):
        """MNSPublisher stores credentials and initializes empty queue cache."""
        pub = MNSPublisher(
            endpoint='https://pub.mns.aliyuncs.com',
            access_key_id='ak',
            access_key_secret='sk'
        )

        assert pub.endpoint == 'https://pub.mns.aliyuncs.com'
        assert pub.access_key_id == 'ak'
        assert pub.access_key_secret == 'sk'
        assert pub._queues == {}

    def test_topic_path(self):
        """topic_path returns queue_name directly (MNS compatibility)."""
        pub = MNSPublisher('https://ep', 'ak', 'sk')

        result = pub.topic_path('my-project', 'my-queue')

        assert result == 'my-queue'

    def test_publish_success(self):
        """publish creates MNSService, publishes JSON, returns PublishFuture."""
        pub = MNSPublisher('https://ep', 'ak', 'sk')
        data = json.dumps({'job': '1'}).encode('utf-8')

        with patch('mns_service.MNSService') as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.publish_message.return_value = 'msg-pub-1'
            mock_svc_cls.return_value = mock_svc

            future = pub.publish('audio-jobs', data)

        assert isinstance(future, PublishFuture)
        assert future.result() == 'msg-pub-1'
        mock_svc_cls.assert_called_once_with('https://ep', 'ak', 'sk', 'audio-jobs')
        mock_svc.publish_message.assert_called_once_with({'job': '1'})

    def test_publish_caches_queue(self):
        """publish reuses cached MNSService for same queue name."""
        pub = MNSPublisher('https://ep', 'ak', 'sk')
        data = json.dumps({'x': 1}).encode('utf-8')

        with patch('mns_service.MNSService') as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.publish_message.return_value = 'msg-1'
            mock_svc_cls.return_value = mock_svc

            pub.publish('q1', data)
            pub.publish('q1', data)

        # MNSService created only once for the same queue
        mock_svc_cls.assert_called_once()
        assert mock_svc.publish_message.call_count == 2

    def test_publish_raw_bytes_fallback(self):
        """publish base64-encodes non-JSON bytes."""
        pub = MNSPublisher('https://ep', 'ak', 'sk')
        raw_data = b'\x00\x01\x02\xff'

        with patch('mns_service.MNSService') as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.publish_message.return_value = 'msg-raw'
            mock_svc_cls.return_value = mock_svc

            future = pub.publish('q2', raw_data)

        # Should encode as base64 in 'raw' key
        call_data = mock_svc.publish_message.call_args[0][0]
        assert 'raw' in call_data
        assert call_data['raw'] == base64.b64encode(raw_data).decode('utf-8')
        assert future.result() == 'msg-raw'

    def test_publish_creates_separate_queues(self):
        """publish creates separate MNSService instances for different queue names."""
        pub = MNSPublisher('https://ep', 'ak', 'sk')
        data = json.dumps({'x': 1}).encode('utf-8')

        with patch('mns_service.MNSService') as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.publish_message.return_value = 'msg-1'
            mock_svc_cls.return_value = mock_svc

            pub.publish('queue-a', data)
            pub.publish('queue-b', data)

        # Two different queues -> two MNSService instances
        assert mock_svc_cls.call_count == 2


# ─────────────────────────────────────────────────
# PublishFuture
# ─────────────────────────────────────────────────

class TestPublishFuture:
    """Tests for PublishFuture."""

    def test_result_success(self):
        """result() returns message_id when publish succeeded."""
        future = PublishFuture('msg-ok')
        assert future.result() == 'msg-ok'

    def test_result_with_timeout_param(self):
        """result() accepts timeout parameter (Pub/Sub compatibility)."""
        future = PublishFuture('msg-t')
        assert future.result(timeout=60) == 'msg-t'

    def test_result_failure(self):
        """result() raises Exception when message_id is None."""
        future = PublishFuture(None)
        with pytest.raises(Exception, match="Failed to publish message"):
            future.result()
