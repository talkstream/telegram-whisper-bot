#!/usr/bin/env python3
"""
Unit tests for v3.6.0 features:
- _build_format_prompt() (single source of truth for LLM prompt)
- format_dialogue() (diarization formatting)
- send_as_file() (TelegramService)
- transcribe_with_diarization() (mocked Fun-ASR)
- /output and /dialogue command handlers
- Proper nouns & sibilant rules in prompt

Run with: cd alibaba && python -m pytest tests/test_audio_v36.py -v
"""
import os
import sys
import json
import tempfile
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

from audio import AudioService
from telegram import TelegramService


# ============== Fixtures ==============

@pytest.fixture
def audio_service():
    """Create AudioService with qwen-asr backend and mock OSS config."""
    return AudioService(
        whisper_backend='qwen-asr',
        alibaba_api_key='test-key',
        oss_config={
            'bucket': 'test-bucket',
            'endpoint': 'oss-test.aliyuncs.com',
            'access_key_id': 'test-ak',
            'access_key_secret': 'test-sk',
        }
    )


@pytest.fixture
def tg_service():
    """Create TelegramService with mock token."""
    return TelegramService(bot_token='test-token')


# ============== _build_format_prompt Tests ==============

class TestBuildFormatPrompt:
    """Test prompt construction — single source of truth for Qwen & Gemini."""

    def test_basic_prompt_contains_rules(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "тестовый текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "Исправь ЯВНЫЕ ошибки" in prompt
        assert "ИМЕНА И ФАМИЛИИ" in prompt
        assert "ШИПЯЩИЕ/СВИСТЯЩИЕ" in prompt
        assert "топонимов" in prompt

    def test_code_tags_on(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=True, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "Оберни ВЕСЬ текст в теги <code>" in prompt

    def test_code_tags_off(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "НЕ используй теги <code>" in prompt

    def test_yo_on(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "Сохраняй букву ё" in prompt

    def test_yo_off(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=False,
            is_chunked=False, is_dialogue=False
        )
        assert "Заменяй все буквы ё на е" in prompt

    def test_chunked_instruction(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=True, is_dialogue=False
        )
        assert "собран из нескольких последовательных фрагментов" in prompt

    def test_dialogue_instruction(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=True
        )
        assert "ФОРМАТ ДИАЛОГА" in prompt
        assert "тире" in prompt
        assert "НЕ добавляй метки" in prompt

    def test_chunked_and_dialogue_combined(self, audio_service):
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=True, is_dialogue=True
        )
        assert "собран из нескольких" in prompt
        assert "ФОРМАТ ДИАЛОГА" in prompt

    def test_proper_nouns_conservative_rule(self, audio_service):
        """Verify conservative approach to proper nouns."""
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "максимально консервативен с именами" in prompt
        assert "НЕ заменяй его на похожее" in prompt

    def test_sibilant_rule(self, audio_service):
        """Verify ASR sibilant confusion handling."""
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "ш/щ/ч/ж/с/з/ц" in prompt
        assert "В сомнительных случаях" in prompt


# ============== format_dialogue Tests ==============

class TestFormatDialogue:
    """Test dialogue formatting from diarized segments."""

    def test_two_speakers(self, audio_service):
        segments = [
            {'speaker_id': 0, 'text': 'Привет!'},
            {'speaker_id': 1, 'text': 'Привет, как дела?'},
            {'speaker_id': 0, 'text': 'Хорошо, спасибо.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 3
        assert lines[0] == '\u2014 Привет!'
        assert lines[1] == '\u2014 Привет, как дела?'
        assert lines[2] == '\u2014 Хорошо, спасибо.'

    def test_merge_consecutive_same_speaker(self, audio_service):
        segments = [
            {'speaker_id': 0, 'text': 'Первая часть.'},
            {'speaker_id': 0, 'text': 'Вторая часть.'},
            {'speaker_id': 1, 'text': 'Ответ.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 2
        assert 'Первая часть. Вторая часть.' in lines[0]

    def test_skip_empty_text(self, audio_service):
        segments = [
            {'speaker_id': 0, 'text': 'Текст'},
            {'speaker_id': 1, 'text': ''},
            {'speaker_id': 1, 'text': '  '},
            {'speaker_id': 2, 'text': 'Ответ'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 2

    def test_empty_segments(self, audio_service):
        result = audio_service.format_dialogue([])
        assert result == ''

    def test_single_speaker(self, audio_service):
        segments = [
            {'speaker_id': 0, 'text': 'Монолог часть 1.'},
            {'speaker_id': 0, 'text': 'Монолог часть 2.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 1
        assert '\u2014 Монолог часть 1. Монолог часть 2.' == lines[0]


# ============== send_as_file Tests ==============

class TestSendAsFile:
    """Test TelegramService.send_as_file()."""

    @patch.object(TelegramService, 'send_document')
    def test_send_as_file_creates_txt(self, mock_send_doc, tg_service):
        mock_send_doc.return_value = {'ok': True}
        result = tg_service.send_as_file(123, "Текст для файла", caption="Подпись")
        assert result == {'ok': True}
        mock_send_doc.assert_called_once()
        args = mock_send_doc.call_args
        # Check file path ends with .txt
        assert args[0][1].endswith('.txt')
        assert args[1]['caption'] == 'Подпись'

    @patch.object(TelegramService, 'send_document')
    def test_send_as_file_cleans_up(self, mock_send_doc, tg_service):
        mock_send_doc.return_value = {'ok': True}
        tg_service.send_as_file(123, "Test content")
        # Verify temp file was cleaned up
        args = mock_send_doc.call_args
        file_path = args[0][1]
        assert not os.path.exists(file_path)


# ============== transcribe_with_diarization Tests ==============

class TestTranscribeWithDiarization:
    """Test Fun-ASR diarization (mocked API calls)."""

    @patch('requests.get')
    @patch('requests.post')
    def test_diarization_success(self, mock_post, mock_get, audio_service):
        """Test successful diarization flow."""
        # Mock OSS upload
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        # Mock API responses
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-task-123'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/result.json'}]
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{
                'sentences': [
                    {'speaker_id': 0, 'text': 'Привет', 'begin_time': 0, 'end_time': 1000},
                    {'speaker_id': 1, 'text': 'Привет, как дела?', 'begin_time': 1000, 'end_time': 3000},
                ]
            }]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is not None
        assert len(segments) == 2
        assert segments[0]['speaker_id'] == 0
        assert segments[1]['text'] == 'Привет, как дела?'

    def test_diarization_no_api_key(self):
        """Test graceful failure when no API key."""
        svc = AudioService(whisper_backend='qwen-asr', alibaba_api_key=None)
        # Clear env var to ensure no fallback
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': ''}, clear=False):
            raw_text, segments = svc.transcribe_with_diarization('/tmp/test.mp3')
        assert raw_text is None
        assert segments == []

    def test_diarization_oss_failure(self, audio_service):
        """Test fallback when OSS upload fails."""
        audio_service._oss_bucket = None  # Force OSS failure
        audio_service.oss_config = {}

        raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        assert raw_text is None
        assert segments == []


# ============== format_text_with_qwen is_dialogue Tests ==============

class TestFormatWithDialogue:
    """Test that is_dialogue parameter is properly propagated."""

    @patch('requests.post')
    def test_qwen_with_is_dialogue(self, mock_post, audio_service):
        """Verify is_dialogue triggers dialogue instruction in prompt."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'output': {'text': 'formatted dialogue text'}
        }
        mock_post.return_value = mock_response

        result = audio_service.format_text_with_qwen(
            "word " * 20,
            is_dialogue=True
        )
        assert result == 'formatted dialogue text'

        # Verify prompt contained dialogue instruction
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        prompt = payload['input']['messages'][0]['content']
        assert "ФОРМАТ ДИАЛОГА" in prompt


# ============== SLSJsonFormatter Tests ==============

class TestSLSJsonFormatter:
    """Test that formatter was renamed from Stackdriver."""

    def test_setup_logging_works(self):
        """Verify setup_logging creates SLS formatter."""
        from utility import UtilityService
        # Should not raise
        UtilityService.setup_logging('test-component')

    def test_no_stackdriver_reference(self):
        """Verify StackdriverJsonFormatter is fully renamed."""
        from utility import UtilityService
        import inspect
        source = inspect.getsource(UtilityService.setup_logging)
        assert 'Stackdriver' not in source
        assert 'SLS' in source


# ============== Upload to OSS with URL Tests ==============

class TestUploadToOssWithUrl:
    """Test _upload_to_oss_with_url for diarization."""

    def test_upload_returns_key_and_url(self, audio_service):
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://bucket.oss.com/signed'
        audio_service._oss_bucket = mock_bucket

        # Create a temp file
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            f.write(b'fake audio')
            tmp_path = f.name

        try:
            key, url = audio_service._upload_to_oss_with_url(tmp_path)
            assert key is not None
            assert key.startswith('diarization/')
            assert url == 'https://bucket.oss.com/signed'
            mock_bucket.put_object_from_file.assert_called_once()
        finally:
            os.unlink(tmp_path)

    def test_upload_fails_without_oss(self):
        svc = AudioService(whisper_backend='qwen-asr')
        key, url = svc._upload_to_oss_with_url('/tmp/test.mp3')
        assert key is None
        assert url is None


# ============== Diarization Model Tests ==============

class TestDiarizationModel:
    """Verify diarization uses fun-asr-mtl (multilingual, incl. Russian)."""

    @patch('requests.get')
    @patch('requests.post')
    def test_diarization_uses_fun_asr_mtl(self, mock_post, mock_get, audio_service):
        """Verify payload contains model=fun-asr-mtl, not fun-asr."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-task-456'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/result.json'}]
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{
                'sentences': [
                    {'speaker_id': 0, 'text': 'Тест', 'begin_time': 0, 'end_time': 1000},
                ]
            }]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        audio_service.transcribe_with_diarization('/tmp/test.mp3')

        # Verify the POST payload uses fun-asr-mtl
        post_call = mock_post.call_args
        payload = post_call[1]['json']
        assert payload['model'] == 'fun-asr-mtl', \
            f"Expected fun-asr-mtl, got {payload['model']}"


# ============== ASR language_hints Tests ==============

class TestASRLanguageHints:
    """Verify qwen3-asr-flash sends language_hints."""

    @patch('requests.post')
    def test_asr_payload_contains_language_hints(self, mock_post, audio_service):
        """Verify qwen3-asr-flash payload includes language_hints: ['ru']."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'output': {
                'choices': [{
                    'message': {
                        'content': [{'text': 'Тестовая транскрипция'}]
                    }
                }]
            }
        }
        mock_post.return_value = mock_response

        # Create a minimal fake audio file
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            f.write(b'\xff\xfb\x90\x00' + b'\x00' * 100)  # Minimal MP3 header
            tmp_path = f.name

        try:
            audio_service._transcribe_single_qwen_asr(tmp_path)

            # Verify the POST payload
            post_call = mock_post.call_args
            payload = post_call[1]['json']
            asr_options = payload['parameters']['asr_options']
            assert asr_options.get('language_hints') == ['ru'], \
                f"Expected language_hints=['ru'], got {asr_options}"
        finally:
            os.unlink(tmp_path)


# ============== MNS Fallback Tests ==============

class TestMNSFallback:
    """Test queue_audio_async sync fallback when MNS is unavailable."""

    def test_missing_mns_endpoint_uses_sync_fallback(self):
        """When MNS_ENDPOINT is empty, should use sync fallback with warning (not error)."""
        # Import the module-level variables and function
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

        import main as webhook_main

        message = {
            'chat': {'id': 123},
            'from': {'id': 456},
            'message_id': 789,
        }
        user = {'user_id': '456', 'balance_minutes': 100}

        with patch.object(webhook_main, 'MNS_ENDPOINT', None), \
             patch.object(webhook_main, 'ALIBABA_ACCESS_KEY', 'test-key'), \
             patch.object(webhook_main, 'ALIBABA_SECRET_KEY', 'test-secret'), \
             patch.object(webhook_main, 'process_audio_sync', return_value='ok') as mock_sync, \
             patch.object(webhook_main, 'get_db_service') as mock_db, \
             patch.object(webhook_main, 'get_telegram_service') as mock_tg:

            result = webhook_main.queue_audio_async(
                message, user, 'file-id', 'voice', 30
            )

            # Should fall back to sync
            mock_sync.assert_called_once()
            assert result == 'ok'
            # Should NOT create a job in DB (MNS check is before job creation)
            mock_db.return_value.create_job.assert_not_called()

    def test_mns_publish_failure_cleans_up_job(self):
        """When MNS publish fails, should mark job failed and use sync fallback."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

        import main as webhook_main

        message = {
            'chat': {'id': 123},
            'from': {'id': 456},
            'message_id': 789,
        }
        user = {'user_id': '456', 'balance_minutes': 100}

        mock_publisher = MagicMock()
        mock_publisher.publish.side_effect = ConnectionError("MNS unreachable")

        with patch.object(webhook_main, 'MNS_ENDPOINT', 'https://mns.example.com'), \
             patch.object(webhook_main, 'ALIBABA_ACCESS_KEY', 'test-key'), \
             patch.object(webhook_main, 'ALIBABA_SECRET_KEY', 'test-secret'), \
             patch.object(webhook_main, 'process_audio_sync', return_value='ok') as mock_sync, \
             patch.object(webhook_main, 'get_db_service') as mock_db, \
             patch.object(webhook_main, 'get_telegram_service') as mock_tg, \
             patch('services.mns_service.MNSPublisher', return_value=mock_publisher):

            result = webhook_main.queue_audio_async(
                message, user, 'file-id', 'voice', 30
            )

            # Should fall back to sync
            mock_sync.assert_called_once()
            assert result == 'ok'
            # Job was created (MNS config was valid)
            mock_db.return_value.create_job.assert_called_once()
            # Job should be marked as failed
            mock_db.return_value.update_job_status.assert_called_once()
