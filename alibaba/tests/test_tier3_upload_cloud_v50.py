#!/usr/bin/env python3
"""
Unit tests for v5.0.0 Tier 3 features:
- Cloud drive URL detection and import (_is_cloud_drive_url, _resolve_download_url, _handle_url_import)
- Mini App endpoints (_serve_upload_page, _validate_init_data, _handle_signed_url_request, _handle_process_upload)
- Audio-processor download routing (_download_from_oss, _download_from_url, _download_and_convert)
- /upload command

Run with: python -m pytest alibaba/tests/test_tier3_upload_cloud_v50.py -v
"""
import hashlib
import hmac
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, mock_open
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

import pytest


# === Fixtures ===

@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token-123')
    monkeypatch.setenv('OWNER_ID', '999')
    monkeypatch.setenv('TABLESTORE_ENDPOINT', 'https://test.ots.aliyuncs.com')
    monkeypatch.setenv('TABLESTORE_INSTANCE', 'test')
    monkeypatch.setenv('ALIBABA_ACCESS_KEY', 'test-ak')
    monkeypatch.setenv('ALIBABA_SECRET_KEY', 'test-sk')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('MNS_ENDPOINT', 'https://mns.test.aliyuncs.com')
    monkeypatch.setenv('OSS_ENDPOINT', 'oss-eu-central-1.aliyuncs.com')
    monkeypatch.setenv('OSS_BUCKET', 'test-bucket')


# === Cloud Drive URL Detection ===

class TestCloudDriveUrlDetection:
    """Test _is_cloud_drive_url pattern matching."""

    def test_yandex_disk_d_link(self):
        import main
        assert main._is_cloud_drive_url('https://disk.yandex.ru/d/abc123') is True

    def test_yandex_disk_i_link(self):
        import main
        assert main._is_cloud_drive_url('https://disk.yandex.com/i/xyz456') is True

    def test_google_drive_link(self):
        import main
        assert main._is_cloud_drive_url('https://drive.google.com/file/d/1ABC/view?usp=sharing') is True

    def test_dropbox_link(self):
        import main
        assert main._is_cloud_drive_url('https://www.dropbox.com/s/abc123/file.mp3?dl=0') is True

    def test_dropbox_scl_link(self):
        import main
        assert main._is_cloud_drive_url('https://dropbox.com/scl/fi/abc/file.mp3') is True

    def test_plain_text_not_url(self):
        import main
        assert main._is_cloud_drive_url('Hello world') is False

    def test_other_url_not_cloud(self):
        import main
        assert main._is_cloud_drive_url('https://example.com/file.mp3') is False

    def test_empty_string(self):
        import main
        assert main._is_cloud_drive_url('') is False


# === Cloud Drive URL Resolution ===

class TestResolveDownloadUrl:
    """Test _resolve_download_url for each service."""

    def test_google_drive_resolved(self):
        import main
        result = main._resolve_download_url('https://drive.google.com/file/d/1ABC_def/view?usp=sharing')
        assert result == 'https://drive.google.com/uc?export=download&id=1ABC_def'

    def test_dropbox_dl0_to_dl1(self):
        import main
        result = main._resolve_download_url('https://www.dropbox.com/s/abc123/file.mp3?dl=0')
        assert result == 'https://www.dropbox.com/s/abc123/file.mp3?dl=1'

    def test_dropbox_no_dl_param(self):
        import main
        result = main._resolve_download_url('https://www.dropbox.com/s/abc123/file.mp3')
        assert result is not None
        assert 'dl=1' in result

    def test_yandex_disk_api_call(self):
        import main
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'href': 'https://download.yandex.net/file123'}
        with patch.object(requests, 'get', return_value=mock_resp) as mock_get:
            result = main._resolve_download_url('https://disk.yandex.ru/d/abc123')
        assert result == 'https://download.yandex.net/file123'
        mock_get.assert_called_once()
        assert 'cloud-api.yandex.net' in mock_get.call_args[0][0]

    def test_yandex_disk_api_failure(self):
        import main
        import requests
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch.object(requests, 'get', return_value=mock_resp):
            result = main._resolve_download_url('https://disk.yandex.ru/d/abc123')
        assert result is None

    def test_unknown_url_returns_none(self):
        import main
        result = main._resolve_download_url('https://example.com/file.mp3')
        assert result is None


# === Cloud Drive Import Handler ===

class TestHandleUrlImport:
    """Test _handle_url_import end-to-end flow."""

    def _make_message(self, text='https://drive.google.com/file/d/1ABC/view'):
        return {
            'chat': {'id': 12345},
            'from': {'id': 67890},
            'text': text,
        }

    def test_insufficient_balance_rejected(self):
        import main
        user = {'balance_minutes': 0, 'settings': '{}'}
        msg = self._make_message()
        tg = MagicMock()
        with patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=MagicMock()):
            result = main._handle_url_import(msg, user, 'https://drive.google.com/file/d/1ABC/view')
        assert result == 'insufficient_balance'
        tg.send_message.assert_called_once()
        assert '0 мин' in str(tg.send_message.call_args)

    def test_unsupported_url_rejected(self):
        import main
        user = {'balance_minutes': 10, 'settings': '{}'}
        msg = self._make_message('https://example.com/file.mp3')
        tg = MagicMock()
        with patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=MagicMock()), \
             patch.object(main, '_resolve_download_url', return_value=None):
            result = main._handle_url_import(msg, user, 'https://example.com/file.mp3')
        assert result == 'unsupported_url'

    def test_google_drive_url_queued(self):
        import main
        user = {'balance_minutes': 100, 'settings': '{}'}
        msg = self._make_message()
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        db = MagicMock()
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = True
        mock_mns_cls = MagicMock()
        mock_pub_cls = MagicMock(return_value=mock_publisher)
        with patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=db), \
             patch.object(main, 'MNS_ENDPOINT', 'https://mns.test'), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', 'test-ak'), \
             patch.object(main, 'ALIBABA_SECRET_KEY', 'test-sk'), \
             patch.dict('sys.modules', {'services.mns_service': MagicMock(
                 MNSService=mock_mns_cls, MNSPublisher=mock_pub_cls)}):
            result = main._handle_url_import(msg, user, 'https://drive.google.com/file/d/1ABC/view')
        assert result == 'url_import_queued'
        db.create_job.assert_called_once()
        job_data = db.create_job.call_args[0][0]
        assert job_data['file_type'] == 'url_import'
        # Status message should mention Google Drive
        status_call = tg.send_message.call_args
        assert 'Google Drive' in str(status_call)

    def test_yandex_disk_detected(self):
        import main
        user = {'balance_minutes': 100, 'settings': '{}'}
        msg = self._make_message('https://disk.yandex.ru/d/abc123')
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = True
        mock_pub_cls = MagicMock(return_value=mock_publisher)
        with patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=MagicMock()), \
             patch.object(main, '_resolve_download_url', return_value='https://download.yandex.net/file'), \
             patch.object(main, 'MNS_ENDPOINT', 'https://mns.test'), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', 'test-ak'), \
             patch.object(main, 'ALIBABA_SECRET_KEY', 'test-sk'), \
             patch.dict('sys.modules', {'services.mns_service': MagicMock(
                 MNSService=MagicMock(), MNSPublisher=mock_pub_cls)}):
            result = main._handle_url_import(msg, user, 'https://disk.yandex.ru/d/abc123')
        assert result == 'url_import_queued'
        assert 'Яндекс.Диск' in str(tg.send_message.call_args)

    def test_mns_unavailable_fails_gracefully(self):
        import main
        user = {'balance_minutes': 100, 'settings': '{}'}
        msg = self._make_message()
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        db = MagicMock()
        with patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=db), \
             patch.object(main, 'MNS_ENDPOINT', ''), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', ''):
            result = main._handle_url_import(msg, user, 'https://drive.google.com/file/d/1ABC/view')
        assert result == 'url_import_failed'


# === Mini App: _serve_upload_page ===

class TestServeUploadPage:
    """Test Mini App fallback page for direct browser access."""

    def test_returns_html(self):
        import main
        result = main._serve_upload_page()
        assert result['statusCode'] == 200
        assert 'text/html' in result['headers']['Content-Type']
        assert '<!DOCTYPE html>' in result['body']

    def test_shows_branded_splash(self):
        import main
        result = main._serve_upload_page()
        assert 'editorialsrobot' in result['body']
        assert 'tg.close()' in result['body']

    def test_cache_headers(self):
        import main
        result = main._serve_upload_page()
        assert 'no-store' in result['headers']['Cache-Control']


# === Mini App: _validate_init_data ===

class TestValidateInitData:
    """Test Telegram initData HMAC-SHA256 validation."""

    def _make_init_data(self, bot_token='test-token-123', user_id=67890):
        """Create valid initData with correct HMAC-SHA256 signature."""
        user_json = json.dumps({'id': user_id, 'first_name': 'Test'})
        auth_date = str(int(time.time()))
        params = {
            'user': user_json,
            'auth_date': auth_date,
            'query_id': 'test_query_123',
        }
        # Build data-check-string
        check_items = [f"{k}={v}" for k, v in sorted(params.items())]
        data_check_string = '\n'.join(check_items)

        # HMAC-SHA256
        secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        params['hash'] = computed_hash

        return urlencode(params)

    def test_valid_init_data_returns_user_id(self):
        import main
        token = 'test-token-123'
        init_data = self._make_init_data(bot_token=token)
        with patch.object(main, 'TELEGRAM_BOT_TOKEN', token):
            result = main._validate_init_data(init_data)
        assert result == 67890

    def test_empty_init_data_returns_none(self):
        import main
        assert main._validate_init_data('') is None

    def test_invalid_hash_returns_none(self):
        import main
        result = main._validate_init_data('user=%7B%22id%22%3A123%7D&auth_date=1234&hash=badhash')
        assert result is None

    def test_missing_hash_returns_none(self):
        import main
        result = main._validate_init_data('user=%7B%22id%22%3A123%7D&auth_date=1234')
        assert result is None

    def test_wrong_token_returns_none(self):
        import main
        # Create init data with different token
        init_data = self._make_init_data(bot_token='wrong-token')
        result = main._validate_init_data(init_data)
        assert result is None


# === Mini App: _handle_signed_url_request ===

class TestHandleSignedUrlRequest:
    """Test OSS signed URL generation endpoint."""

    def test_invalid_auth_returns_403(self):
        import main
        body = {'init_data': '', 'ext': '.mp3'}
        with patch.object(main, '_validate_init_data', return_value=None):
            result = main._handle_signed_url_request(body, {})
        assert result['statusCode'] == 403
        resp = json.loads(result['body'])
        assert 'error' in resp

    def test_unsupported_extension_returns_400(self):
        import main
        body = {'init_data': 'valid', 'ext': '.exe'}
        with patch.object(main, '_validate_init_data', return_value=123):
            result = main._handle_signed_url_request(body, {})
        assert result['statusCode'] == 400
        resp = json.loads(result['body'])
        assert '.exe' in resp['error']

    def test_valid_request_returns_url(self):
        import main
        import oss2
        body = {'init_data': 'valid', 'ext': '.mp3'}
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed?Signature=abc'
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(oss2, 'Auth', return_value=MagicMock()), \
             patch.object(oss2, 'Bucket', return_value=mock_bucket):
            result = main._handle_signed_url_request(body, {})
        assert result['statusCode'] == 200
        resp = json.loads(result['body'])
        assert 'put_url' in resp
        assert 'oss_key' in resp
        assert resp['oss_key'].startswith('uploads/123/')
        assert resp['oss_key'].endswith('.mp3')

    def test_cors_headers_present(self):
        import main
        import oss2
        body = {'init_data': 'valid', 'ext': '.mp3'}
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed'
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(oss2, 'Auth', return_value=MagicMock()), \
             patch.object(oss2, 'Bucket', return_value=mock_bucket):
            result = main._handle_signed_url_request(body, {})
        assert result['headers']['Access-Control-Allow-Origin'] == '*'

    def test_oss_error_returns_500(self):
        import main
        import oss2
        body = {'init_data': 'valid', 'ext': '.mp3'}
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(oss2, 'Auth', side_effect=Exception('OSS connection failed')):
            result = main._handle_signed_url_request(body, {})
        assert result['statusCode'] == 500


# === Mini App: _handle_process_upload ===

class TestHandleProcessUpload:
    """Test processing job creation from OSS upload."""

    def test_invalid_auth_returns_403(self):
        import main
        body = {'init_data': '', 'oss_key': 'uploads/123/file.mp3'}
        with patch.object(main, '_validate_init_data', return_value=None):
            result = main._handle_process_upload(body, {})
        assert result['statusCode'] == 403

    def test_wrong_user_oss_key_returns_400(self):
        import main
        body = {'init_data': 'valid', 'oss_key': 'uploads/999/file.mp3'}
        with patch.object(main, '_validate_init_data', return_value=123):
            result = main._handle_process_upload(body, {})
        assert result['statusCode'] == 400
        resp = json.loads(result['body'])
        assert 'Invalid OSS key' in resp['error']

    def test_valid_upload_creates_job(self):
        import main
        body = {'init_data': 'valid', 'oss_key': 'uploads/123/abc.mp3', 'filename': 'interview.mp3'}
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        db = MagicMock()
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = True
        mock_pub_cls = MagicMock(return_value=mock_publisher)
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=db), \
             patch.object(main, 'MNS_ENDPOINT', 'https://mns.test'), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', 'test-ak'), \
             patch.object(main, 'ALIBABA_SECRET_KEY', 'test-sk'), \
             patch.dict('sys.modules', {'services.mns_service': MagicMock(
                 MNSService=MagicMock(), MNSPublisher=mock_pub_cls)}):
            result = main._handle_process_upload(body, {})
        assert result['statusCode'] == 200
        resp = json.loads(result['body'])
        assert resp['ok'] is True
        assert 'job_id' in resp
        # Verify job created with oss_upload type
        db.create_job.assert_called_once()
        job = db.create_job.call_args[0][0]
        assert job['file_type'] == 'oss_upload'
        assert job['file_id'] == 'uploads/123/abc.mp3'
        assert job['duration'] == 0

    def test_no_async_processor_returns_500(self):
        """Neither AUDIO_PROCESSOR_URL nor MNS configured → 500."""
        import main
        body = {'init_data': 'valid', 'oss_key': 'uploads/123/abc.mp3', 'filename': 'test.mp3'}
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        db = MagicMock()
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=db), \
             patch.object(main, 'MNS_ENDPOINT', ''), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', ''), \
             patch.dict(os.environ, {}, clear=False), \
             patch.dict(os.environ, {'AUDIO_PROCESSOR_URL': ''}, clear=False):
            # Remove AUDIO_PROCESSOR_URL if present
            os.environ.pop('AUDIO_PROCESSOR_URL', None)
            result = main._handle_process_upload(body, {})
        assert result['statusCode'] == 500
        db.update_job.assert_called_once()

    def test_mns_not_configured_returns_500(self):
        import main
        body = {'init_data': 'valid', 'oss_key': 'uploads/123/abc.mp3', 'filename': 'test.mp3'}
        tg = MagicMock()
        tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        db = MagicMock()
        with patch.object(main, '_validate_init_data', return_value=123), \
             patch.object(main, 'get_telegram_service', return_value=tg), \
             patch.object(main, 'get_db_service', return_value=db), \
             patch.object(main, 'MNS_ENDPOINT', ''), \
             patch.object(main, 'ALIBABA_ACCESS_KEY', ''):
            result = main._handle_process_upload(body, {})
        assert result['statusCode'] == 500


# === /upload command ===

class TestUploadCommand:
    """Test /upload command handler."""

    def test_sends_mini_app_button_with_github_pages_url(self):
        import main
        tg = MagicMock()
        user = {'balance_minutes': 100, 'settings': '{}'}
        result = main._cmd_upload(12345, 67890, '/upload', user, tg, MagicMock())
        tg.send_message.assert_called_once()
        call_args = tg.send_message.call_args
        reply_markup = call_args[1]['reply_markup']
        web_app_url = reply_markup['inline_keyboard'][0][0]['web_app']['url']
        assert 'github.io' in web_app_url
        assert 'upload.html' in web_app_url


# === Handler HTTP routing ===

class TestHandlerRouting:
    """Test handler() routes to Mini App endpoints."""

    def test_get_upload_serves_html(self):
        import main
        event = {
            'requestContext': {'http': {'method': 'GET', 'path': '/upload'}},
            'headers': {},
        }
        with patch.object(main, '_serve_upload_page', return_value={'statusCode': 200, 'body': '<html>'}) as mock_serve:
            result = main.handler(event, None)
        mock_serve.assert_called_once()
        assert result['statusCode'] == 200

    def test_post_signed_url_routes(self):
        import main
        event = {
            'requestContext': {'http': {'method': 'POST', 'path': '/api/signed-url'}},
            'headers': {'content-type': 'application/json'},
            'body': json.dumps({'init_data': 'test', 'ext': '.mp3'}),
        }
        with patch.object(main, '_handle_signed_url_request', return_value={'statusCode': 200}) as mock_handler:
            result = main.handler(event, None)
        mock_handler.assert_called_once()

    def test_post_process_routes(self):
        import main
        event = {
            'requestContext': {'http': {'method': 'POST', 'path': '/api/process'}},
            'headers': {'content-type': 'application/json'},
            'body': json.dumps({'oss_key': 'uploads/1/f.mp3', 'init_data': 'test'}),
        }
        with patch.object(main, '_handle_process_upload', return_value={'statusCode': 200}) as mock_handler:
            result = main.handler(event, None)
        mock_handler.assert_called_once()

    def test_get_health_check_v500(self):
        import main
        event = {
            'requestContext': {'http': {'method': 'GET', 'path': '/'}},
            'headers': {},
        }
        result = main.handler(event, None)
        body = json.loads(result['body'])
        assert body['version'] == '5.0.0'


# === Audio-processor: download routing ===

class TestDownloadRouting:
    """Test _download_and_convert file_type routing."""

    @pytest.fixture
    def mock_services(self):
        tg = MagicMock()
        tg.get_file_path.return_value = 'file/path.ogg'
        tg.download_file.return_value = '/tmp/test.ogg'
        audio = MagicMock()
        audio.prepare_audio_for_asr.return_value = '/tmp/test.mp3'
        return tg, audio

    def test_oss_upload_routes_to_oss(self, mock_services):
        import handler
        tg, audio = mock_services
        with patch.object(handler, '_download_from_oss', return_value='/tmp/oss_file.mp3') as mock_oss:
            local, converted = handler._download_and_convert(
                tg, audio, 'uploads/123/abc.mp3', 12345, None,
                file_type='oss_upload'
            )
        mock_oss.assert_called_once_with('uploads/123/abc.mp3')
        tg.get_file_path.assert_not_called()

    def test_url_import_routes_to_url(self, mock_services):
        import handler
        tg, audio = mock_services
        with patch.object(handler, '_download_from_url', return_value='/tmp/url_file.mp3') as mock_url:
            local, converted = handler._download_and_convert(
                tg, audio, 'https://download.example.com/file.mp3', 12345, None,
                file_type='url_import'
            )
        mock_url.assert_called_once_with('https://download.example.com/file.mp3')
        tg.get_file_path.assert_not_called()

    def test_default_routes_to_telegram(self, mock_services):
        import handler
        tg, audio = mock_services
        local, converted = handler._download_and_convert(
            tg, audio, 'AgACAgIAAxkBAAI', 12345, None, file_type=None
        )
        tg.get_file_path.assert_called_once()
        tg.download_file.assert_called_once()

    def test_voice_type_routes_to_telegram(self, mock_services):
        import handler
        tg, audio = mock_services
        local, converted = handler._download_and_convert(
            tg, audio, 'AgACAgIAAxkBAAI', 12345, None, file_type='voice'
        )
        tg.get_file_path.assert_called_once()


# === Audio-processor: _download_from_oss ===

class TestDownloadFromOss:
    """Test OSS download in audio-processor."""

    def test_downloads_to_tmp(self):
        import handler
        import oss2
        mock_bucket = MagicMock()
        with patch.object(oss2, 'Auth', return_value=MagicMock()), \
             patch.object(oss2, 'Bucket', return_value=mock_bucket):
            result = handler._download_from_oss('uploads/123/abc.mp3')
        assert result.startswith('/tmp/')
        assert result.endswith('.mp3')
        mock_bucket.get_object_to_file.assert_called_once()

    def test_preserves_extension(self):
        import handler
        import oss2
        mock_bucket = MagicMock()
        with patch.object(oss2, 'Auth', return_value=MagicMock()), \
             patch.object(oss2, 'Bucket', return_value=mock_bucket):
            result = handler._download_from_oss('uploads/123/abc.wav')
        assert result.endswith('.wav')


# === Audio-processor: _download_from_url ===

class TestDownloadFromUrl:
    """Test URL download in audio-processor."""

    def test_downloads_streaming(self):
        import handler
        import requests
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-length': '1000'}
        mock_response.iter_content.return_value = [b'data' * 250]
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests, 'get', return_value=mock_response), \
             patch('builtins.open', mock_open()):
            result = handler._download_from_url('https://example.com/file.mp3')
        assert result.startswith('/tmp/')
        assert result.endswith('.mp3')

    def test_rejects_large_content_length(self):
        import handler
        import requests
        mock_response = MagicMock()
        mock_response.headers = {'content-length': str(200 * 1024 * 1024)}
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests, 'get', return_value=mock_response):
            with pytest.raises(Exception, match='too large'):
                handler._download_from_url('https://example.com/large.mp3')

    def test_detects_extension_from_url(self):
        import handler
        import requests
        mock_response = MagicMock()
        mock_response.headers = {'content-length': '1000'}
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = MagicMock()
        with patch.object(requests, 'get', return_value=mock_response), \
             patch('builtins.open', mock_open()):
            result = handler._download_from_url('https://example.com/file.wav')
        assert result.endswith('.wav')


# === URL detection in handle_message ===

class TestUrlDetectionInHandleMessage:
    """Test that cloud drive URLs are detected in handle_message."""

    def test_cloud_url_triggers_import(self):
        import main
        message = {
            'chat': {'id': 12345},
            'from': {'id': 67890},
            'text': 'https://disk.yandex.ru/d/abc123',
        }
        mock_db = MagicMock()
        mock_db.get_user.return_value = {'balance_minutes': 100, 'settings': '{}'}
        with patch.object(main, 'get_telegram_service', return_value=MagicMock()), \
             patch.object(main, 'get_db_service', return_value=mock_db), \
             patch.object(main, '_handle_url_import', return_value='url_import_queued') as mock_import:
            result = main.handle_message(message)
        mock_import.assert_called_once()
        assert result == 'url_import_queued'

    def test_regular_text_not_imported(self):
        import main
        message = {
            'chat': {'id': 12345},
            'from': {'id': 67890},
            'text': 'Hello world',
        }
        mock_db = MagicMock()
        mock_db.get_user.return_value = {'balance_minutes': 100, 'settings': '{}'}
        with patch.object(main, 'get_telegram_service', return_value=MagicMock()), \
             patch.object(main, 'get_db_service', return_value=mock_db):
            result = main.handle_message(message)
        assert result == 'message_received'


# === Constants ===

class TestTier3Constants:
    """Test Tier 3 constants and patterns."""

    def test_cloud_drive_patterns_keys(self):
        import main
        assert 'yandex_disk' in main.CLOUD_DRIVE_PATTERNS
        assert 'google_drive' in main.CLOUD_DRIVE_PATTERNS
        assert 'dropbox' in main.CLOUD_DRIVE_PATTERNS

    def test_upload_page_html_exists(self):
        import main
        assert len(main.UPLOAD_PAGE_HTML) > 100
        assert '<!DOCTYPE html>' in main.UPLOAD_PAGE_HTML
        assert '__API_BASE__' in main.UPLOAD_PAGE_HTML
