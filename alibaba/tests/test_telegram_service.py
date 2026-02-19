#!/usr/bin/env python3
"""
Unit tests for TelegramService (alibaba/shared/telegram.py).

Covers methods NOT tested elsewhere:
- send_message: success, error handling, parse_mode, reply_markup
- edit_message_text: success, error handling
- delete_message: success, error handling
- send_chat_action: success, exception fallback
- get_file_path: success, API error, request exception
- download_file: success, request exception, suffix detection
- send_document: success, error handling
- send_invoice: success, error handling, extra kwargs
- answer_pre_checkout_query: ok=True, ok=False with error, request exception
- answer_callback_query: success, error handling
- _post_with_retry: 429 retry, 5xx retry, connection error retry
- format_progress_bar: normal, boundary values
- format_time_estimate: seconds, minutes, hours, edge cases
- Legacy wrappers: send_message, edit_message_text, get_file_path, download_file, send_document

Already tested in test_audio_v35.py / test_audio_v36.py (NOT duplicated):
- send_as_file (TestSendAsFile)
- send_long_message (TestSendLongMessage)
- Timeout constants and per-method timeouts (TestTelegramTimeouts)

Run: cd alibaba && python -m pytest tests/test_telegram_service.py -v
"""
import os
import sys
import json
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest
import requests

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

from telegram import (
    TelegramService,
    init_telegram_service,
    get_telegram_service,
    send_message as legacy_send_message,
    edit_message_text as legacy_edit_message_text,
    get_file_path as legacy_get_file_path,
    download_file as legacy_download_file,
    send_document as legacy_send_document,
)


# ============== Fixtures ==============

@pytest.fixture
def tg():
    """Create TelegramService with test token."""
    return TelegramService(bot_token='test-token-123')


def _ok_response(result=None):
    """Helper: build a successful Telegram API mock response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'ok': True, 'result': result or {}}
    resp.raise_for_status = MagicMock()
    return resp


def _error_response(status_code=400, text='Bad Request'):
    """Helper: build an HTTP error response that triggers raise_for_status."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    http_err = requests.exceptions.HTTPError(response=resp)
    resp.raise_for_status.side_effect = http_err
    return resp


# ============== send_message Tests ==============

class TestSendMessage:
    """Test TelegramService.send_message()."""

    @patch('requests.Session.post')
    def test_send_message_success(self, mock_post, tg):
        """Successful send returns parsed JSON."""
        mock_post.return_value = _ok_response({'message_id': 42})
        result = tg.send_message(123, 'Hello')
        assert result == {'ok': True, 'result': {'message_id': 42}}
        payload = mock_post.call_args[1]['json']
        assert payload['chat_id'] == 123
        assert payload['text'] == 'Hello'

    @patch('requests.Session.post')
    def test_send_message_with_parse_mode(self, mock_post, tg):
        """parse_mode is included in payload when provided."""
        mock_post.return_value = _ok_response()
        tg.send_message(123, '<b>bold</b>', parse_mode='HTML')
        payload = mock_post.call_args[1]['json']
        assert payload['parse_mode'] == 'HTML'

    @patch('requests.Session.post')
    def test_send_message_with_reply_markup(self, mock_post, tg):
        """reply_markup is JSON-serialized in payload."""
        mock_post.return_value = _ok_response()
        markup = {'inline_keyboard': [[{'text': 'btn', 'callback_data': 'cb'}]]}
        tg.send_message(123, 'Choose', reply_markup=markup)
        payload = mock_post.call_args[1]['json']
        assert json.loads(payload['reply_markup']) == markup

    @patch('requests.Session.post')
    def test_send_message_http_error_returns_none(self, mock_post, tg):
        """HTTP error returns None instead of raising."""
        mock_post.return_value = _error_response(400, 'Bad Request')
        result = tg.send_message(123, 'test')
        assert result is None

    @patch('requests.Session.post')
    def test_send_message_connection_error_returns_none(self, mock_post, tg):
        """Connection error after retries returns None."""
        mock_post.side_effect = requests.exceptions.ConnectionError('refused')
        result = tg.send_message(123, 'test')
        assert result is None


# ============== edit_message_text Tests ==============

class TestEditMessageText:
    """Test TelegramService.edit_message_text()."""

    @patch('requests.Session.post')
    def test_edit_success(self, mock_post, tg):
        """Successful edit returns parsed JSON."""
        mock_post.return_value = _ok_response({'message_id': 10})
        result = tg.edit_message_text(123, 10, 'Updated')
        assert result['ok'] is True
        payload = mock_post.call_args[1]['json']
        assert payload['message_id'] == 10
        assert payload['text'] == 'Updated'

    @patch('requests.Session.post')
    def test_edit_with_parse_mode_and_markup(self, mock_post, tg):
        """Both parse_mode and reply_markup are passed correctly."""
        mock_post.return_value = _ok_response()
        markup = {'inline_keyboard': []}
        tg.edit_message_text(123, 10, 'text', parse_mode='Markdown', reply_markup=markup)
        payload = mock_post.call_args[1]['json']
        assert payload['parse_mode'] == 'Markdown'
        assert json.loads(payload['reply_markup']) == markup

    @patch('requests.Session.post')
    def test_edit_error_returns_none(self, mock_post, tg):
        """HTTP error returns None."""
        mock_post.return_value = _error_response(400)
        result = tg.edit_message_text(123, 10, 'text')
        assert result is None


# ============== delete_message Tests ==============

class TestDeleteMessage:
    """Test TelegramService.delete_message()."""

    @patch('requests.Session.post')
    def test_delete_success(self, mock_post, tg):
        """Successful delete returns True."""
        mock_post.return_value = _ok_response()
        assert tg.delete_message(123, 10) is True

    @patch('requests.Session.post')
    def test_delete_error_returns_false(self, mock_post, tg):
        """HTTP error returns False."""
        mock_post.return_value = _error_response(400)
        assert tg.delete_message(123, 10) is False


# ============== send_chat_action Tests ==============

class TestSendChatAction:
    """Test TelegramService.send_chat_action()."""

    @patch('requests.Session.post')
    def test_chat_action_success(self, mock_post, tg):
        """Successful action returns True."""
        mock_post.return_value = MagicMock(status_code=200)
        assert tg.send_chat_action(123, 'typing') is True
        payload = mock_post.call_args[1]['json']
        assert payload['action'] == 'typing'

    @patch('requests.Session.post')
    def test_chat_action_exception_returns_false(self, mock_post, tg):
        """Exception is swallowed, returns False (fire-and-forget)."""
        mock_post.side_effect = requests.exceptions.Timeout('timeout')
        assert tg.send_chat_action(123, 'typing') is False


# ============== get_file_path Tests ==============

class TestGetFilePath:
    """Test TelegramService.get_file_path()."""

    @patch('requests.Session.get')
    def test_get_file_path_success(self, mock_get, tg):
        """Returns file_path from Telegram API response."""
        mock_get.return_value = _ok_response({'file_path': 'voice/file_42.ogg'})
        result = tg.get_file_path('file-id-42')
        assert result == 'voice/file_42.ogg'
        assert mock_get.call_args[1]['params'] == {'file_id': 'file-id-42'}

    @patch('requests.Session.get')
    def test_get_file_path_api_error(self, mock_get, tg):
        """Telegram API returns ok=False — should return None."""
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {'ok': False, 'error_code': 400, 'description': 'Bad Request'}
        mock_get.return_value = resp
        assert tg.get_file_path('bad-id') is None

    @patch('requests.Session.get')
    def test_get_file_path_request_exception(self, mock_get, tg):
        """Network error returns None."""
        mock_get.side_effect = requests.exceptions.ConnectionError('refused')
        assert tg.get_file_path('file-id') is None


# ============== download_file Tests ==============

class TestDownloadFile:
    """Test TelegramService.download_file()."""

    @patch('requests.Session.get')
    def test_download_success(self, mock_get, tg):
        """Downloads file content to a temp file and returns its path."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'audio-data-chunk']
        mock_get.return_value = mock_resp

        result = tg.download_file('voice/file_42.ogg')
        assert result is not None
        assert result.endswith('.ogg')
        assert os.path.exists(result)
        with open(result, 'rb') as f:
            assert f.read() == b'audio-data-chunk'
        os.unlink(result)

    @patch('requests.Session.get')
    def test_download_preserves_extension(self, mock_get, tg):
        """Extension is taken from file_path; defaults to .ogg if missing."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'data']
        mock_get.return_value = mock_resp

        # .mp3 extension preserved
        result = tg.download_file('voice/file.mp3')
        assert result.endswith('.mp3')
        os.unlink(result)

        # No extension defaults to .ogg
        result = tg.download_file('voice/file_no_ext')
        assert result.endswith('.ogg')
        os.unlink(result)

    @patch('requests.Session.get')
    def test_download_request_error_returns_none(self, mock_get, tg):
        """Network error returns None."""
        mock_get.side_effect = requests.exceptions.ConnectionError('refused')
        assert tg.download_file('voice/file.ogg') is None

    @patch('requests.Session.get')
    def test_download_uses_target_dir(self, mock_get, tg):
        """File is downloaded to the specified target_dir."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'data']
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            result = tg.download_file('voice/file.ogg', target_dir=tmpdir)
            assert result.startswith(tmpdir)
            os.unlink(result)


# ============== send_document Tests ==============

class TestSendDocument:
    """Test TelegramService.send_document()."""

    @patch('requests.Session.post')
    def test_send_document_success(self, mock_post, tg):
        """Sends file and returns JSON response."""
        mock_post.return_value = _ok_response({'message_id': 55})

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('test content')
            tmp_path = f.name

        try:
            result = tg.send_document(123, tmp_path, caption='Result')
            assert result['ok'] is True
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['data']['chat_id'] == '123'
            assert call_kwargs['data']['caption'] == 'Result'
        finally:
            os.unlink(tmp_path)

    @patch('requests.Session.post')
    def test_send_document_no_caption(self, mock_post, tg):
        """Caption is omitted from data when empty."""
        mock_post.return_value = _ok_response()

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('content')
            tmp_path = f.name

        try:
            tg.send_document(123, tmp_path)
            call_kwargs = mock_post.call_args[1]
            assert 'caption' not in call_kwargs['data']
        finally:
            os.unlink(tmp_path)

    @patch('requests.Session.post')
    def test_send_document_http_error_returns_none(self, mock_post, tg):
        """HTTP error returns None."""
        mock_post.return_value = _error_response(413, 'Request Entity Too Large')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('big content')
            tmp_path = f.name

        try:
            result = tg.send_document(123, tmp_path)
            assert result is None
        finally:
            os.unlink(tmp_path)

    def test_send_document_missing_file_returns_none(self, tg):
        """Non-existent file returns None (FileNotFoundError caught)."""
        result = tg.send_document(123, '/tmp/nonexistent_file_abc123.txt')
        assert result is None


# ============== send_invoice Tests ==============

class TestSendInvoice:
    """Test TelegramService.send_invoice()."""

    @patch('requests.Session.post')
    def test_send_invoice_success(self, mock_post, tg):
        """Successful invoice returns parsed JSON."""
        mock_post.return_value = _ok_response({'message_id': 77})
        prices = [{'label': '30 minutes', 'amount': 100}]
        result = tg.send_invoice(
            123, 'Minutes Pack', 'Get 30 minutes', 'pay_30',
            'XTR', prices
        )
        assert result['ok'] is True
        payload = mock_post.call_args[1]['json']
        assert payload['chat_id'] == 123
        assert payload['currency'] == 'XTR'
        assert payload['prices'] == prices

    @patch('requests.Session.post')
    def test_send_invoice_extra_kwargs(self, mock_post, tg):
        """Additional kwargs are merged into invoice params."""
        mock_post.return_value = _ok_response()
        tg.send_invoice(
            123, 'Title', 'Desc', 'pay_x', 'XTR', [],
            provider_token='tok', photo_url='https://example.com/img.png'
        )
        payload = mock_post.call_args[1]['json']
        assert payload['provider_token'] == 'tok'
        assert payload['photo_url'] == 'https://example.com/img.png'

    @patch('requests.Session.post')
    def test_send_invoice_error_returns_none(self, mock_post, tg):
        """HTTP error returns None."""
        mock_post.return_value = _error_response(400)
        result = tg.send_invoice(123, 'T', 'D', 'p', 'XTR', [])
        assert result is None


# ============== answer_pre_checkout_query Tests ==============

class TestAnswerPreCheckoutQuery:
    """Test TelegramService.answer_pre_checkout_query()."""

    @patch('requests.Session.post')
    def test_answer_ok(self, mock_post, tg):
        """Answering ok=True returns True."""
        mock_post.return_value = _ok_response()
        assert tg.answer_pre_checkout_query('q-123') is True
        payload = mock_post.call_args[1]['json']
        assert payload['pre_checkout_query_id'] == 'q-123'
        assert payload['ok'] is True

    @patch('requests.Session.post')
    def test_answer_not_ok_with_error(self, mock_post, tg):
        """Answering ok=False includes error_message."""
        mock_post.return_value = _ok_response()
        assert tg.answer_pre_checkout_query('q-456', ok=False, error_message='Out of stock') is True
        payload = mock_post.call_args[1]['json']
        assert payload['ok'] is False
        assert payload['error_message'] == 'Out of stock'

    @patch('requests.Session.post')
    def test_answer_error_returns_false(self, mock_post, tg):
        """HTTP error returns False."""
        mock_post.return_value = _error_response(500)
        assert tg.answer_pre_checkout_query('q-789') is False


# ============== answer_callback_query Tests ==============

class TestAnswerCallbackQuery:
    """Test TelegramService.answer_callback_query()."""

    @patch('requests.Session.post')
    def test_answer_callback_success(self, mock_post, tg):
        """Successful callback answer returns True."""
        mock_post.return_value = _ok_response()
        assert tg.answer_callback_query('cb-1', text='Done', show_alert=True) is True
        payload = mock_post.call_args[1]['json']
        assert payload['callback_query_id'] == 'cb-1'
        assert payload['text'] == 'Done'
        assert payload['show_alert'] is True

    @patch('requests.Session.post')
    def test_answer_callback_minimal(self, mock_post, tg):
        """Minimal call (no text, no alert) only sends callback_query_id."""
        mock_post.return_value = _ok_response()
        tg.answer_callback_query('cb-2')
        payload = mock_post.call_args[1]['json']
        assert payload == {'callback_query_id': 'cb-2'}

    @patch('requests.Session.post')
    def test_answer_callback_error_returns_false(self, mock_post, tg):
        """HTTP error returns False."""
        mock_post.return_value = _error_response(400)
        assert tg.answer_callback_query('cb-3') is False


# ============== _post_with_retry Tests ==============

class TestPostWithRetry:
    """Test retry logic in _post_with_retry()."""

    @patch('time.sleep')
    @patch('requests.Session.post')
    def test_retry_on_429(self, mock_post, mock_sleep, tg):
        """429 triggers retry, succeeds on second attempt."""
        rate_limited = MagicMock(status_code=429, headers={'Retry-After': '1'})
        success = _ok_response()
        mock_post.side_effect = [rate_limited, success]

        result = tg._post_with_retry('https://api.test/endpoint', json={})
        assert result == success
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch('time.sleep')
    @patch('requests.Session.post')
    def test_retry_on_500(self, mock_post, mock_sleep, tg):
        """5xx triggers retry with backoff."""
        server_error = MagicMock(status_code=500, headers={})
        success = _ok_response()
        mock_post.side_effect = [server_error, success]

        result = tg._post_with_retry('https://api.test/endpoint', json={})
        assert result == success
        # Default backoff for first attempt is RETRY_BACKOFF[0] = 1
        mock_sleep.assert_called_once_with(1)

    @patch('time.sleep')
    @patch('requests.Session.post')
    def test_retry_on_connection_error(self, mock_post, mock_sleep, tg):
        """ConnectionError triggers retry; raises after MAX_RETRIES."""
        mock_post.side_effect = requests.exceptions.ConnectionError('refused')

        with pytest.raises(requests.exceptions.ConnectionError):
            tg._post_with_retry('https://api.test/endpoint', json={})

        assert mock_post.call_count == 3  # MAX_RETRIES = 3

    @patch('time.sleep')
    @patch('requests.Session.post')
    def test_retry_exhausted_returns_last_response(self, mock_post, mock_sleep, tg):
        """All retries exhausted on 5xx returns last response (not raises)."""
        error_resp = MagicMock(status_code=502, headers={})
        mock_post.return_value = error_resp

        result = tg._post_with_retry('https://api.test/endpoint', json={})
        assert result == error_resp
        assert mock_post.call_count == 3

    @patch('requests.Session.post')
    def test_no_retry_on_4xx(self, mock_post, tg):
        """4xx (except 429) returns immediately, no retry."""
        client_error = MagicMock(status_code=400, headers={})
        mock_post.return_value = client_error

        result = tg._post_with_retry('https://api.test/endpoint', json={})
        assert result == client_error
        assert mock_post.call_count == 1


# ============== format_progress_bar Tests ==============

class TestFormatProgressBar:
    """Test TelegramService.format_progress_bar()."""

    def test_zero_percent(self, tg):
        result = tg.format_progress_bar(0)
        assert result == '[' + '░' * 20 + '] 0%'

    def test_hundred_percent(self, tg):
        result = tg.format_progress_bar(100)
        assert result == '[' + '▓' * 20 + '] 100%'

    def test_fifty_percent(self, tg):
        result = tg.format_progress_bar(50)
        assert result == '[' + '▓' * 10 + '░' * 10 + '] 50%'

    def test_negative_clamped_to_zero(self, tg):
        result = tg.format_progress_bar(-10)
        assert '0%' in result

    def test_over_hundred_clamped(self, tg):
        result = tg.format_progress_bar(150)
        assert '100%' in result

    def test_custom_width(self, tg):
        result = tg.format_progress_bar(50, width=10)
        assert result == '[' + '▓' * 5 + '░' * 5 + '] 50%'


# ============== format_time_estimate Tests ==============

class TestFormatTimeEstimate:
    """Test TelegramService.format_time_estimate()."""

    def test_seconds_remaining(self, tg):
        result = tg.format_time_estimate(10, 40)
        assert result == '~30 сек. осталось'

    def test_minutes_remaining(self, tg):
        result = tg.format_time_estimate(60, 240)
        assert result == '~3:00 осталось'

    def test_hours_remaining(self, tg):
        result = tg.format_time_estimate(0, 7200)
        assert result == '~2ч 0м осталось'

    def test_zero_total_returns_empty(self, tg):
        assert tg.format_time_estimate(10, 0) == ''

    def test_negative_elapsed_returns_empty(self, tg):
        assert tg.format_time_estimate(-1, 60) == ''

    def test_elapsed_exceeds_total(self, tg):
        """When elapsed > total, remaining is clamped to 0."""
        result = tg.format_time_estimate(100, 50)
        assert result == '~0 сек. осталось'


# ============== send_progress_update Tests ==============

class TestSendProgressUpdate:
    """Test TelegramService.send_progress_update()."""

    @patch.object(TelegramService, 'edit_message_text')
    def test_progress_update_basic(self, mock_edit, tg):
        """Progress update edits message with stage + bar."""
        mock_edit.return_value = _ok_response()
        tg.send_progress_update(123, 10, 'Transcribing...', 60)
        text = mock_edit.call_args[0][2]
        assert 'Transcribing...' in text
        assert '60%' in text

    @patch.object(TelegramService, 'edit_message_text')
    def test_progress_update_with_time_estimate(self, mock_edit, tg):
        """Time estimate is appended when provided."""
        mock_edit.return_value = _ok_response()
        tg.send_progress_update(123, 10, 'Working', 50, time_estimate='~30 сек. осталось')
        text = mock_edit.call_args[0][2]
        assert '~30 сек. осталось' in text


# ============== Legacy Wrappers Tests ==============

class TestLegacyWrappers:
    """Test module-level backward compatibility functions."""

    def test_legacy_functions_return_none_without_init(self):
        """Legacy functions return None when service is not initialized."""
        import telegram as tg_mod
        original = tg_mod._telegram_service
        tg_mod._telegram_service = None
        try:
            assert legacy_send_message(1, 'hi') is None
            assert legacy_edit_message_text(1, 1, 'x') is None
            assert legacy_get_file_path('f') is None
            assert legacy_download_file('p') is None
            assert legacy_send_document(1, 'p') is None
        finally:
            tg_mod._telegram_service = original

    def test_init_and_get_telegram_service(self):
        """init_telegram_service creates instance, get_telegram_service returns it."""
        import telegram as tg_mod
        original = tg_mod._telegram_service
        try:
            svc = init_telegram_service('new-token')
            assert isinstance(svc, TelegramService)
            assert get_telegram_service() is svc
            assert svc.bot_token == 'new-token'
        finally:
            tg_mod._telegram_service = original

    @patch('requests.Session.post')
    def test_legacy_send_message_delegates(self, mock_post):
        """Legacy send_message delegates to the global service instance."""
        import telegram as tg_mod
        original = tg_mod._telegram_service
        try:
            svc = init_telegram_service('tok')
            mock_post.return_value = _ok_response({'message_id': 1})
            result = legacy_send_message(999, 'hello', parse_mode='HTML')
            assert result is not None
            assert result['ok'] is True
        finally:
            tg_mod._telegram_service = original


# ============== Constructor Tests ==============

class TestConstructor:
    """Test TelegramService initialization."""

    def test_api_url_format(self, tg):
        assert tg.api_url == 'https://api.telegram.org/bottest-token-123'

    def test_file_url_format(self, tg):
        assert tg.file_url == 'https://api.telegram.org/file/bottest-token-123'

    def test_session_is_created(self, tg):
        assert isinstance(tg.session, requests.Session)

    def test_close_session(self, tg):
        """close() calls session.close()."""
        with patch.object(tg.session, 'close') as mock_close:
            tg.close()
            mock_close.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
