#!/usr/bin/env python3
"""
Unit tests for v5.1.0 stability fixes:
- JSON parsing guards (DashScope polling, ASR, Gemini diarization, LLM)
- content-length parsing guard
- OSS cleanup on job creation failure
- Balance reservation (atomic deduction at queue time)
- LLM timeout (300s, no fallback on timeout)
- MIME validation on cloud drive import
- Signed URL expiry (30 min)
- DashScope session pooling (ASR + LLM methods)

Run with: python -m pytest alibaba/tests/test_stability_fixes_v51.py -v
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, PropertyMock, call

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

import pytest


# === Fixtures ===

@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('OWNER_ID', '999')
    monkeypatch.setenv('TABLESTORE_ENDPOINT', 'https://test.ots.aliyuncs.com')
    monkeypatch.setenv('TABLESTORE_INSTANCE', 'test')
    monkeypatch.setenv('ALIBABA_ACCESS_KEY', 'test-ak')
    monkeypatch.setenv('ALIBABA_SECRET_KEY', 'test-sk')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('LLM_BACKEND', 'qwen')
    monkeypatch.setenv('MNS_ENDPOINT', 'https://mns.test.com')


@pytest.fixture
def audio_service():
    from audio import AudioService
    return AudioService(whisper_backend='qwen-asr', alibaba_api_key='test-key')


# === Tier 1.1: JSON Parsing Guard Tests ===

class TestJsonParsingGuard:
    """DashScope response.json() should not crash on malformed responses."""

    def test_submit_response_malformed_json(self, audio_service):
        """Malformed submit response → return None, not crash."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "not json"
        mock_session.post.return_value = mock_response
        audio_service._session = mock_session

        result = audio_service._submit_async_transcription(
            'https://example.com/audio.mp3',
            'fun-asr-mtl',
            {'language_hints': ['ru']},
            'test-api-key',
            debug_prefix='pass1'
        )
        assert result is None
        assert audio_service._diarization_debug.get('pass1_result') == 'malformed_submit_json'

    def test_poll_response_malformed_json(self, audio_service):
        """Malformed poll response → continue polling, not crash."""
        mock_session = MagicMock()

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.json.return_value = {'output': {'task_id': 'test-task-123'}}
        mock_session.post.return_value = submit_resp

        # First poll: malformed JSON → continue; second poll: success
        poll_bad = MagicMock()
        poll_bad.status_code = 200
        poll_bad.json.side_effect = ValueError("Bad JSON")

        poll_good = MagicMock()
        poll_good.status_code = 200
        poll_good.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/trans.json'}]
            }
        }

        trans_resp = MagicMock()
        trans_resp.json.return_value = {'transcripts': [{'text': 'hello'}]}

        mock_session.get.side_effect = [poll_bad, poll_good, trans_resp]
        audio_service._session = mock_session

        with patch('time.sleep'):
            result = audio_service._submit_async_transcription(
                'https://example.com/audio.mp3',
                'fun-asr-mtl',
                {'language_hints': ['ru']},
                'test-api-key',
                poll_interval=1,
                max_wait=10,
            )
        assert result is not None

    def test_transcription_response_malformed_json(self, audio_service):
        """Malformed transcription URL response → return None."""
        mock_session = MagicMock()

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.json.return_value = {'output': {'task_id': 'test-task-123'}}
        mock_session.post.return_value = submit_resp

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/trans.json'}]
            }
        }

        trans_resp = MagicMock()
        trans_resp.json.side_effect = ValueError("Not JSON")

        mock_session.get.side_effect = [poll_resp, trans_resp]
        audio_service._session = mock_session

        with patch('time.sleep'):
            result = audio_service._submit_async_transcription(
                'https://example.com/audio.mp3',
                'fun-asr-mtl',
                {'language_hints': ['ru']},
                'test-api-key',
                poll_interval=1,
                max_wait=10,
                debug_prefix='pass1'
            )
        assert result is None
        assert audio_service._diarization_debug.get('pass1_result') == 'malformed_transcription_json'

    def test_error_response_malformed_json(self, audio_service):
        """Non-200 submit with malformed error body → graceful handling."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.side_effect = ValueError("Not JSON")
        mock_session.post.return_value = mock_response
        audio_service._session = mock_session

        result = audio_service._submit_async_transcription(
            'https://example.com/audio.mp3',
            'fun-asr-mtl',
            {'language_hints': ['ru']},
            'test-api-key',
            debug_prefix='pass1'
        )
        assert result is None


class TestJsonGuardsGemini:
    """Gemini diarization JSON guard."""

    def test_gemini_diarization_malformed_json(self, audio_service):
        """Gemini diarization: malformed JSON → returns (None, [])."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("Invalid JSON")

        with patch('requests.post', return_value=mock_resp), \
             patch.dict(os.environ, {'GOOGLE_API_KEY': 'test-key'}):
            result = audio_service._diarize_gemini('/tmp/test.mp3')
            assert result == (None, [])


class TestJsonGuardsAsr:
    """Qwen ASR JSON guards."""

    @patch('requests.post')
    def test_qwen_asr_200_malformed_json(self, mock_post, audio_service, tmp_path):
        """Qwen ASR: 200 but malformed JSON → RuntimeError."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b'\xff\xfb\x90\x00' * 100)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_resp

        with pytest.raises(RuntimeError, match="malformed JSON"):
            audio_service._transcribe_single_qwen_asr(str(audio_file))

    @patch('requests.post')
    def test_qwen_asr_error_malformed_json(self, mock_post, audio_service, tmp_path):
        """Qwen ASR: non-200 with malformed error body → still raises RuntimeError."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b'\xff\xfb\x90\x00' * 100)

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_resp

        with pytest.raises(RuntimeError, match="API error"):
            audio_service._transcribe_single_qwen_asr(str(audio_file))


class TestJsonGuardsLlm:
    """LLM JSON guards (Qwen + AssemblyAI)."""

    @patch('requests.post')
    def test_qwen_llm_malformed_json_returns_original(self, mock_post, audio_service):
        """Qwen LLM: malformed JSON → return original text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_resp

        original = "Тестовый текст для форматирования который достаточно длинный чтобы пройти проверку минимума слов"
        result = audio_service.format_text_with_qwen(original)
        assert result == original

    @patch('requests.post')
    def test_assemblyai_llm_malformed_json_returns_original(self, mock_post, audio_service):
        """AssemblyAI LLM: malformed JSON → return original text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_post.return_value = mock_resp

        original = "Тестовый текст для форматирования который достаточно длинный чтобы пройти проверку минимума слов"
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            result = audio_service.format_text_with_assemblyai(original)
        assert result == original


# === Tier 1.2: Content-Length Parsing Guard Tests ===

class TestContentLengthGuard:
    """content-length header parsing should handle non-integer values."""

    def test_malformed_content_length_does_not_crash(self):
        """Non-integer content-length → ValueError caught, defaults to 0."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {'content-length': 'chunked', 'content-type': 'audio/mpeg'}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'audio_data' * 100]

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/test.mp3')
            except OSError:
                pass  # File writes may fail in test env, content-length check passed

    def test_missing_content_length_defaults_zero(self):
        """Missing content-length → defaults to 0, proceeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {'content-type': 'audio/mpeg'}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'audio_data']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/test.mp3')
            except OSError:
                pass


# === Tier 1.3: OSS Cleanup Tests ===

class TestOssCleanup:
    """OSS objects should be cleaned up when job creation fails."""

    def test_oss_cleanup_on_mns_failure(self):
        """MNS publish failure → OSS object deleted."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

        mock_db = MagicMock()
        mock_db.create_job.return_value = True
        mock_db.update_job.return_value = True

        mock_tg = MagicMock()
        mock_tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}

        mock_bucket = MagicMock()
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = False  # MNS publish fails

        mock_oss2 = MagicMock()
        mock_oss2.Auth.return_value = MagicMock()
        mock_oss2.StsAuth.return_value = MagicMock()
        mock_oss2.Bucket.return_value = mock_bucket

        mock_mns_service = MagicMock()
        mock_mns_publisher_cls = MagicMock(return_value=mock_publisher)

        with patch('main.get_db_service', return_value=mock_db), \
             patch('main.get_telegram_service', return_value=mock_tg), \
             patch('main._validate_init_data', return_value=12345), \
             patch('main.MNS_ENDPOINT', 'https://mns.test.com'), \
             patch('main.ALIBABA_ACCESS_KEY', 'test-ak'), \
             patch('main.ALIBABA_SECRET_KEY', 'test-sk'), \
             patch('main.ALIBABA_SECURITY_TOKEN', None), \
             patch.dict('sys.modules', {
                 'oss2': mock_oss2,
                 'services.mns_service': MagicMock(
                     MNSService=mock_mns_service,
                     MNSPublisher=mock_mns_publisher_cls
                 ),
             }):

            from main import _handle_process_upload
            result = _handle_process_upload(
                {'init_data': 'valid', 'oss_key': 'uploads/12345/abc.mp3',
                 'filename': 'test.mp3'},
                {}
            )

        assert result['statusCode'] == 500
        # Verify OSS cleanup was attempted
        mock_bucket.delete_object.assert_called_once()


# === Tier 2.1: Balance Reservation Tests ===

class TestBalanceReservation:
    """Atomic balance reservation at queue time."""

    def test_reserve_balance_sufficient(self):
        """Sufficient balance → reserved successfully."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        with patch.object(ts, 'get_user', return_value={'balance_minutes': 10}):
            result = ts.reserve_balance(123, 5)
        assert result is True
        ts.client.update_row.assert_called_once()

    def test_reserve_balance_insufficient(self):
        """Insufficient balance → returns False, no DB update."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        with patch.object(ts, 'get_user', return_value={'balance_minutes': 3}):
            result = ts.reserve_balance(123, 5)
        assert result is False
        ts.client.update_row.assert_not_called()

    def test_reserve_balance_zero(self):
        """Zero balance → returns False."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        with patch.object(ts, 'get_user', return_value={'balance_minutes': 0}):
            result = ts.reserve_balance(123, 1)
        assert result is False

    def test_reserve_balance_conflict_retry(self):
        """OTSConditionCheckFail → retry and succeed."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        ts.client.update_row.side_effect = [
            Exception('OTSConditionCheckFail'),
            None
        ]

        with patch.object(ts, 'get_user', return_value={'balance_minutes': 10}), \
             patch('time.sleep'):
            result = ts.reserve_balance(123, 5)
        assert result is True
        assert ts.client.update_row.call_count == 2

    def test_reserve_balance_user_not_found(self):
        """Missing user → returns False."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        with patch.object(ts, 'get_user', return_value=None):
            result = ts.reserve_balance(123, 5)
        assert result is False

    def test_reserve_balance_string_balance(self):
        """String balance (legacy data) → parsed correctly."""
        from tablestore_service import TablestoreService
        ts = TablestoreService.__new__(TablestoreService)
        ts.client = MagicMock()

        with patch.object(ts, 'get_user', return_value={'balance_minutes': '15'}):
            result = ts.reserve_balance(123, 5)
        assert result is True

    def test_queue_audio_insufficient_balance(self):
        """queue_audio_async should reject when reserve_balance fails."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

        mock_db = MagicMock()
        mock_db.reserve_balance.return_value = False

        mock_tg = MagicMock()
        mock_tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}

        with patch('main.get_db_service', return_value=mock_db), \
             patch('main.get_telegram_service', return_value=mock_tg):
            from main import queue_audio_async
            result = queue_audio_async(
                {'chat': {'id': 100}, 'from': {'id': 200}, 'message_id': 1},
                {'balance_minutes': 1},
                'file123', 'telegram', 120,
                status_message_id=42
            )

        assert result == 'insufficient_balance'
        mock_db.create_job.assert_not_called()
        mock_db.reserve_balance.assert_called_once_with(200, 2)

    def test_handler_refunds_on_failure(self):
        """Audio processor should refund reserved minutes on failure."""
        mock_db = MagicMock()
        mock_db.get_job.return_value = None
        mock_db.update_job.return_value = True
        mock_db.get_user.return_value = {'balance_minutes': 10, 'settings': '{}'}
        mock_db.update_user_balance.return_value = True

        mock_tg = MagicMock()
        mock_tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        mock_tg.edit_message_text.return_value = {'ok': True}
        mock_tg.send_chat_action.return_value = True

        mock_audio = MagicMock()
        mock_audio.prepare_audio_for_asr.side_effect = Exception("Conversion failed")

        with patch('handler.get_db_service', return_value=mock_db), \
             patch('handler.get_telegram_service', return_value=mock_tg), \
             patch('handler.get_audio_service', return_value=mock_audio), \
             patch('handler.os.remove'):

            from handler import process_job
            result = process_job({
                'job_id': 'test-job',
                'user_id': '200',
                'chat_id': 100,
                'file_id': 'file123',
                'duration': 120,
                'reserved_minutes': 2,
            })

        assert result['ok'] is False
        # Should have refunded 2 minutes
        mock_db.update_user_balance.assert_called_with(200, 2)

    def test_handler_no_refund_when_no_reservation(self):
        """Jobs without reserved_minutes (e.g. duration=0 docs) → no refund."""
        mock_db = MagicMock()
        mock_db.get_job.return_value = None
        mock_db.update_job.return_value = True
        mock_db.get_user.return_value = {'balance_minutes': 10, 'settings': '{}'}

        mock_tg = MagicMock()
        mock_tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
        mock_tg.edit_message_text.return_value = {'ok': True}
        mock_tg.send_chat_action.return_value = True

        mock_audio = MagicMock()
        mock_audio.prepare_audio_for_asr.side_effect = Exception("Conversion failed")

        with patch('handler.get_db_service', return_value=mock_db), \
             patch('handler.get_telegram_service', return_value=mock_tg), \
             patch('handler.get_audio_service', return_value=mock_audio), \
             patch('handler.os.remove'):

            from handler import process_job
            result = process_job({
                'job_id': 'test-job',
                'user_id': '200',
                'chat_id': 100,
                'file_id': 'file123',
                'duration': 0,
                # No reserved_minutes key
            })

        assert result['ok'] is False
        # No refund because reserved_minutes defaults to 0
        mock_db.update_user_balance.assert_not_called()


# === Tier 2.2: LLM Fallback Timeout Tests ===

class TestLlmFallbackTimeout:
    """AssemblyAI LLM timeout should be 300s for Gemini 3 Flash (no timeout-based fallback)."""

    @patch('requests.post')
    def test_assemblyai_timeout_is_300s(self, mock_post, audio_service):
        """Verify timeout=300 in format_text_with_assemblyai request."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'formatted text'}, 'finish_reason': 'stop'}],
            'usage': {}
        }
        mock_post.return_value = mock_resp

        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            audio_service.format_text_with_assemblyai(
                "This is a long enough text for formatting with enough words to pass minimum check threshold here",
                _is_fallback=True
            )

        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 300

    @patch('requests.post')
    def test_timeout_returns_original_not_fallback(self, mock_post, audio_service):
        """Timeout should return original text, NOT trigger Qwen fallback."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            result = audio_service.format_text_with_assemblyai(
                "Original text that should be returned on timeout with enough words here",
            )

        assert result == "Original text that should be returned on timeout with enough words here"


# === Tier 2.3: MIME Validation Tests ===

class TestMimeValidation:
    """Cloud drive downloads should reject non-audio content types."""

    def test_html_content_type_rejected(self):
        """text/html response → Exception raised."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'text/html; charset=utf-8',
            'content-length': '1000'
        }
        mock_resp.raise_for_status = MagicMock()

        with patch('requests.get', return_value=mock_resp):
            from handler import _download_from_url
            with pytest.raises(Exception, match='Неподдерживаемый формат'):
                _download_from_url('https://example.com/fake.mp3')

    def test_json_content_type_rejected(self):
        """application/json response → Exception raised (API error page)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'application/json',
            'content-length': '500'
        }
        mock_resp.raise_for_status = MagicMock()

        with patch('requests.get', return_value=mock_resp):
            from handler import _download_from_url
            with pytest.raises(Exception, match='Неподдерживаемый формат'):
                _download_from_url('https://example.com/api/error')

    def test_audio_content_type_accepted(self):
        """audio/mpeg response → passes MIME check."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'audio/mpeg',
            'content-length': '1000'
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'fake_audio_data']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/test.mp3')
            except OSError:
                pass  # File writes may fail, MIME check passed

    def test_octet_stream_accepted(self):
        """application/octet-stream → download proceeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'application/octet-stream',
            'content-length': '1000'
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'fake_data']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/audio')
            except OSError:
                pass

    def test_video_content_type_accepted(self):
        """video/mp4 response → download proceeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'video/mp4',
            'content-length': '5000'
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'fake_video']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/video.mp4')
            except OSError:
                pass

    def test_empty_content_type_passes(self):
        """No content-type header → download proceeds (no validation)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {'content-length': '1000'}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'data']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/file')
            except OSError:
                pass

    def test_application_ogg_accepted(self):
        """application/ogg → download proceeds."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            'content-type': 'application/ogg',
            'content-length': '2000'
        }
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content.return_value = [b'ogg_data']

        with patch('requests.get', return_value=mock_resp), \
             patch('builtins.open', MagicMock()):
            from handler import _download_from_url
            try:
                _download_from_url('https://example.com/audio.ogg')
            except OSError:
                pass


# === Tier 3.1: Signed URL Expiry Tests ===

class TestSignedUrlExpiry:
    """Signed URL expiry should be 30 minutes (1800s)."""

    def test_signed_url_30min_expiry(self):
        """Signed URL should use expires=1800 (30 min)."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed?token=abc'

        mock_oss2 = MagicMock()
        mock_oss2.Auth.return_value = MagicMock()
        mock_oss2.Bucket.return_value = mock_bucket

        with patch('main.get_db_service', return_value=MagicMock()), \
             patch('main.get_telegram_service', return_value=MagicMock()), \
             patch('main._validate_init_data', return_value=12345), \
             patch('main.ALIBABA_ACCESS_KEY', 'test-ak'), \
             patch('main.ALIBABA_SECRET_KEY', 'test-sk'), \
             patch('main.ALIBABA_SECURITY_TOKEN', None), \
             patch.dict('sys.modules', {'oss2': mock_oss2}):

            from main import _handle_signed_url_request
            result = _handle_signed_url_request(
                {'init_data': 'valid', 'ext': '.mp3'},
                {}
            )

        assert result['statusCode'] == 200
        call_args = mock_bucket.sign_url.call_args
        assert call_args[0][2] == 1800  # Third positional arg is expires


# === Tier 3.2: DashScope Session Pooling Tests ===

class TestSessionPooling:
    """DashScope calls should reuse a single HTTP session."""

    def test_http_session_starts_none(self, audio_service):
        """_session attribute starts as None before any access."""
        assert audio_service._session is None

    def test_http_session_reuse(self, audio_service):
        """_http_session returns same session on repeated access."""
        mock_sess = MagicMock()
        audio_service._session = mock_sess
        s1 = audio_service._http_session
        s2 = audio_service._http_session
        assert s1 is s2
        assert s1 is mock_sess

    def test_dashscope_uses_session(self, audio_service):
        """_submit_async_transcription should use self._http_session."""
        mock_session = MagicMock()

        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.json.return_value = {'output': {'task_id': 'task-123'}}

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'result': {'transcription_url': 'https://example.com/trans.json'}
            }
        }

        trans_resp = MagicMock()
        trans_resp.json.return_value = {'text': 'hello'}

        mock_session.post.return_value = submit_resp
        mock_session.get.side_effect = [poll_resp, trans_resp]
        audio_service._session = mock_session

        with patch('time.sleep'):
            result = audio_service._submit_async_transcription(
                'https://example.com/audio.mp3',
                'fun-asr-mtl',
                {'language_hints': ['ru']},
                'test-api-key',
                poll_interval=1,
                max_wait=10,
            )

        # Verify session.post was used for submit
        mock_session.post.assert_called_once()
        # Verify session.get was used for poll + transcription
        assert mock_session.get.call_count == 2
        assert result is not None

    @patch('requests.post')
    def test_qwen_asr_uses_requests_post(self, mock_post, audio_service, tmp_path):
        """Qwen ASR should use requests.post (not session) for single calls."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b'\xff\xfb\x90\x00' * 100)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'output': {'choices': [{'message': {'content': [{'text': 'Распознанный текст'}]}}]}
        }
        mock_post.return_value = mock_resp

        result = audio_service._transcribe_single_qwen_asr(str(audio_file))
        mock_post.assert_called_once()
        assert result == 'Распознанный текст'
