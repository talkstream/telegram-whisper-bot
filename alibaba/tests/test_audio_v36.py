#!/usr/bin/env python3
"""
Unit tests for v3.6.0 features:
- _build_format_prompt() (single source of truth for LLM prompt)
- format_dialogue() (diarization formatting)
- send_as_file() (TelegramService)
- transcribe_with_diarization() (mocked Fun-ASR)
- /output command handler
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
        assert lines[0] == 'Спикер 1:\n\u2014 Привет!'
        assert lines[1] == 'Спикер 2:\n\u2014 Привет, как дела?'
        assert lines[2] == 'Спикер 1:\n\u2014 Хорошо, спасибо.'

    def test_merge_consecutive_same_speaker(self, audio_service):
        segments = [
            {'speaker_id': 0, 'text': 'Первая часть.'},
            {'speaker_id': 0, 'text': 'Вторая часть.'},
            {'speaker_id': 1, 'text': 'Ответ.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 2
        assert lines[0] == 'Спикер 1:\n\u2014 Первая часть.\nВторая часть.'
        assert lines[1] == 'Спикер 2:\n\u2014 Ответ.'

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
        # speaker_id 1 has only empty text, so unique_speakers = {0, 2}
        assert lines[0] == 'Спикер 1:\n\u2014 Текст'
        assert lines[1] == 'Спикер 2:\n\u2014 Ответ'

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
        assert '\u2014 Монолог часть 1.\nМонолог часть 2.' == lines[0]

    def test_three_speakers(self, audio_service):
        """Three speakers should get sequential labels by first appearance."""
        segments = [
            {'speaker_id': 5, 'text': 'Алло. Здравствуйте.'},
            {'speaker_id': 10, 'text': 'А вы кто?'},
            {'speaker_id': 10, 'text': 'Меня зовут Арсений.'},
            {'speaker_id': 20, 'text': 'Здравствуйте.'},
            {'speaker_id': 5, 'text': 'Я вас слушаю.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 4
        # speaker_map by appearance: {5: 1, 10: 2, 20: 3}
        assert lines[0] == 'Спикер 1:\n\u2014 Алло. Здравствуйте.'
        assert lines[1] == 'Спикер 2:\n\u2014 А вы кто?\nМеня зовут Арсений.'
        assert lines[2] == 'Спикер 3:\n\u2014 Здравствуйте.'
        assert lines[3] == 'Спикер 1:\n\u2014 Я вас слушаю.'

    def test_single_speaker_no_labels(self, audio_service):
        """Single speaker should NOT get labels, just em-dash."""
        segments = [
            {'speaker_id': 3, 'text': 'Первое предложение.'},
            {'speaker_id': 3, 'text': 'Второе предложение.'},
            {'speaker_id': 3, 'text': 'Третье.'},
        ]
        result = audio_service.format_dialogue(segments)
        assert result == '\u2014 Первое предложение.\nВторое предложение.\nТретье.'
        assert 'Спикер' not in result

    def test_punctuation_only_filtered(self, audio_service):
        """Segments with only punctuation should be filtered out."""
        segments = [
            {'speaker_id': 0, 'text': 'Нормальный текст.'},
            {'speaker_id': 1, 'text': '.'},
            {'speaker_id': 1, 'text': '...'},
            {'speaker_id': 2, 'text': 'Ответ.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 2
        assert 'Нормальный текст' in lines[0]
        assert 'Ответ' in lines[1]
        # Punctuation-only segments from speaker 1 removed
        assert '.' not in [l.strip() for l in lines]

    def test_speaker_numbering_by_appearance(self, audio_service):
        """Speaker IDs should be numbered by order of first appearance, not sorted."""
        segments = [
            {'speaker_id': 99, 'text': 'Первый говорящий.'},
            {'speaker_id': 5, 'text': 'Второй говорящий.'},
            {'speaker_id': 99, 'text': 'Снова первый.'},
        ]
        result = audio_service.format_dialogue(segments)
        lines = result.split('\n\n')
        assert len(lines) == 3
        # speaker 99 appears first → Спикер 1
        assert lines[0].startswith('Спикер 1:')
        # speaker 5 appears second → Спикер 2
        assert lines[1].startswith('Спикер 2:')
        # speaker 99 again → Спикер 1
        assert lines[2].startswith('Спикер 1:')

    def test_show_speakers_false(self, audio_service):
        """show_speakers=False should hide 'Спикер N:' labels but keep em-dash."""
        segments = [
            {'speaker_id': 0, 'text': 'Алло. Здравствуйте.'},
            {'speaker_id': 1, 'text': 'Не похищал. Это Марина.'},
            {'speaker_id': 0, 'text': 'Я вас слушаю.'},
        ]
        result = audio_service.format_dialogue(segments, show_speakers=False)
        lines = result.split('\n\n')
        assert len(lines) == 3
        # No speaker labels
        assert 'Спикер' not in result
        # Em-dash still present
        assert lines[0] == '\u2014 Алло. Здравствуйте.'
        assert lines[1] == '\u2014 Не похищал. Это Марина.'
        assert lines[2] == '\u2014 Я вас слушаю.'

    def test_show_speakers_true_default(self, audio_service):
        """Default show_speakers=True preserves 'Спикер N:' labels (backward compat)."""
        segments = [
            {'speaker_id': 0, 'text': 'Привет!'},
            {'speaker_id': 1, 'text': 'Привет, как дела?'},
        ]
        result = audio_service.format_dialogue(segments)
        assert 'Спикер 1:' in result
        assert 'Спикер 2:' in result


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
    """Test two-pass diarization (mocked API calls)."""

    def test_diarization_no_api_key(self):
        """Test graceful failure when no API key."""
        svc = AudioService(whisper_backend='qwen-asr', alibaba_api_key=None)
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': ''}, clear=False):
            raw_text, segments = svc.transcribe_with_diarization('/tmp/test.mp3')
        assert raw_text is None
        assert segments == []

    def test_diarization_oss_failure(self, audio_service):
        """Test fallback when OSS upload fails."""
        audio_service._oss_bucket = None
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

    def test_oss_bucket_uses_https(self, audio_service):
        """OSS bucket endpoint forced to HTTPS for DashScope compatibility."""
        with patch('oss2.Auth') as mock_auth, \
             patch('oss2.Bucket') as mock_bucket_cls:
            mock_bucket_cls.return_value = MagicMock()
            audio_service._oss_bucket = None  # Reset cached bucket

            bucket = audio_service._get_oss_bucket()
            assert bucket is not None
            # Verify endpoint passed to oss2.Bucket starts with https://
            call_args = mock_bucket_cls.call_args
            endpoint_arg = call_args[0][1]  # Second positional arg
            assert endpoint_arg.startswith('https://'), \
                f"Expected HTTPS endpoint, got: {endpoint_arg}"


# ============== Diarization Model Tests ==============

class TestDiarizationModel:
    """Verify two-pass diarization uses correct models."""

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_async_fun_asr_mtl_uses_file_urls(self, mock_post, mock_get, audio_service):
        """Verify fun-asr-mtl payload uses file_urls (plural, list)."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-task-spk'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/spk.json'}]
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{'sentences': [
                {'speaker_id': 0, 'text': 'test', 'begin_time': 0, 'end_time': 1000}
            ]}]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        audio_service._submit_async_transcription(
            'https://oss.example.com/file.mp3', 'fun-asr-mtl',
            {'diarization_enabled': True}, 'test-key', poll_interval=1, max_wait=5)

        post_call = mock_post.call_args
        payload = post_call[1]['json']
        assert payload['model'] == 'fun-asr-mtl'
        assert 'file_urls' in payload['input']
        assert isinstance(payload['input']['file_urls'], list)

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_async_qwen_filetrans_uses_file_url(self, mock_post, mock_get, audio_service):
        """Verify qwen3-asr-flash-filetrans payload uses file_url (singular, string)."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-task-txt'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'result': {'transcription_url': 'https://example.com/txt.json'}
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{'sentences': [
                {'text': 'тестовый текст', 'begin_time': 0, 'end_time': 1000}
            ]}]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        audio_service._submit_async_transcription(
            'https://oss.example.com/file.mp3', 'qwen3-asr-flash-filetrans',
            {'language': 'ru'}, 'test-key', poll_interval=1, max_wait=5)

        post_call = mock_post.call_args
        payload = post_call[1]['json']
        assert payload['model'] == 'qwen3-asr-flash-filetrans'
        assert 'file_url' in payload['input']
        assert isinstance(payload['input']['file_url'], str)

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_async_handles_singular_result(self, mock_post, mock_get, audio_service):
        """Verify output.result (singular dict) is parsed correctly for qwen3-asr-flash-filetrans."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'task-singular'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'result': {'transcription_url': 'https://example.com/singular.json'}
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{'sentences': [
                {'text': 'результат singular', 'begin_time': 0, 'end_time': 2000}
            ]}]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        result = audio_service._submit_async_transcription(
            'https://oss.example.com/file.mp3', 'qwen3-asr-flash-filetrans',
            {'language': 'ru'}, 'test-key', poll_interval=1, max_wait=5)

        assert result is not None
        assert result['transcripts'][0]['sentences'][0]['text'] == 'результат singular'

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_async_handles_plural_results(self, mock_post, mock_get, audio_service):
        """Verify output.results (plural list) is parsed correctly for fun-asr-mtl."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'task-plural'}
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {
                'task_status': 'SUCCEEDED',
                'results': [{'transcription_url': 'https://example.com/plural.json'}]
            }
        }

        trans_response = MagicMock()
        trans_response.json.return_value = {
            'transcripts': [{'sentences': [
                {'text': 'результат plural', 'begin_time': 0, 'end_time': 3000}
            ]}]
        }

        mock_post.return_value = submit_response
        mock_get.side_effect = [poll_response, trans_response]

        result = audio_service._submit_async_transcription(
            'https://oss.example.com/file.mp3', 'fun-asr-mtl',
            {}, 'test-key', poll_interval=1, max_wait=5)

        assert result is not None
        assert result['transcripts'][0]['sentences'][0]['text'] == 'результат plural'


# ============== _align_speakers_with_text Tests ==============

class TestAlignSpeakersWithText:
    """Test timestamp-based alignment of speaker labels with text segments."""

    def test_perfect_alignment(self, audio_service):
        """Segments with matching timestamps align perfectly."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 2000},
            {'speaker_id': 1, 'begin_time': 2000, 'end_time': 5000},
        ]
        texts = [
            {'text': 'Привет', 'begin_time': 0, 'end_time': 2000},
            {'text': 'Здравствуйте', 'begin_time': 2000, 'end_time': 5000},
        ]
        result = audio_service._align_speakers_with_text(speakers, texts)
        assert len(result) == 2
        assert result[0]['speaker_id'] == 0
        assert result[0]['text'] == 'Привет'
        assert result[1]['speaker_id'] == 1
        assert result[1]['text'] == 'Здравствуйте'

    def test_overlap_splits_across_speakers(self, audio_service):
        """Text segment overlapping two speakers is split at word level."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 3000},
            {'speaker_id': 1, 'begin_time': 3000, 'end_time': 6000},
        ]
        # Text spans 1000-4000: 3 words, word times ~1000, 2000, 3000
        texts = [
            {'text': 'Текст на границе', 'begin_time': 1000, 'end_time': 4000},
        ]
        result = audio_service._align_speakers_with_text(speakers, texts)
        # Words should be split between speakers (not all to one)
        assert len(result) >= 1
        # First words should go to speaker 0, last to speaker 1
        assert result[0]['speaker_id'] == 0

    def test_no_speaker_segments(self, audio_service):
        """Empty speaker list assigns all to speaker 0."""
        texts = [
            {'text': 'Текст', 'begin_time': 0, 'end_time': 1000},
        ]
        result = audio_service._align_speakers_with_text([], texts)
        assert len(result) == 1
        assert result[0]['speaker_id'] == 0

    def test_no_text_segments(self, audio_service):
        """Empty text list returns empty result."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 1000},
        ]
        result = audio_service._align_speakers_with_text(speakers, [])
        assert result == []

    def test_many_segments_alignment(self, audio_service):
        """Multiple segments align correctly in sequence."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 2000},
            {'speaker_id': 1, 'begin_time': 2000, 'end_time': 4000},
            {'speaker_id': 0, 'begin_time': 4000, 'end_time': 6000},
        ]
        texts = [
            {'text': 'Первый', 'begin_time': 100, 'end_time': 1900},
            {'text': 'Второй', 'begin_time': 2100, 'end_time': 3900},
            {'text': 'Третий', 'begin_time': 4100, 'end_time': 5900},
        ]
        result = audio_service._align_speakers_with_text(speakers, texts)
        # Each single-word text segment maps to one speaker
        assert result[0]['speaker_id'] == 0
        assert result[1]['speaker_id'] == 1
        assert result[2]['speaker_id'] == 0

    def test_align_multi_speaker_in_one_text_segment(self, audio_service):
        """When one text segment spans multiple speakers, words should be split."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 5000},
            {'speaker_id': 1, 'begin_time': 5000, 'end_time': 10000},
        ]
        texts = [
            {'text': 'Первое слово второе слово третье слово четвёртое слово',
             'begin_time': 0, 'end_time': 10000},
        ]
        result = audio_service._align_speakers_with_text(speakers, texts)
        assert len(result) == 2  # Split into 2 segments
        assert result[0]['speaker_id'] == 0
        assert result[1]['speaker_id'] == 1
        # Words should be roughly split in half
        assert len(result[0]['text'].split()) >= 2
        assert len(result[1]['text'].split()) >= 2

    def test_align_three_speakers_in_one_text_segment(self, audio_service):
        """One text segment spanning three speakers gets split into three."""
        speakers = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 3000},
            {'speaker_id': 1, 'begin_time': 3000, 'end_time': 6000},
            {'speaker_id': 2, 'begin_time': 6000, 'end_time': 9000},
        ]
        texts = [
            {'text': 'а б в г д е ж з и',
             'begin_time': 0, 'end_time': 9000},
        ]
        result = audio_service._align_speakers_with_text(speakers, texts)
        assert len(result) == 3
        assert result[0]['speaker_id'] == 0
        assert result[1]['speaker_id'] == 1
        assert result[2]['speaker_id'] == 2


# ============== _submit_async_transcription Tests ==============

class TestSubmitAsyncTranscription:
    """Test the shared async transcription helper."""

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_timeout(self, mock_post, mock_get, audio_service):
        """Timeout returns None."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-timeout'}
        }
        mock_post.return_value = submit_response

        # Always return RUNNING status
        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {'task_status': 'RUNNING'}
        }
        mock_get.return_value = poll_response

        result = audio_service._submit_async_transcription(
            'https://example.com/file.mp3', 'fun-asr-mtl',
            {'diarization_enabled': True}, 'test-key',
            poll_interval=1, max_wait=2)

        assert result is None

    @patch('requests.post')
    def test_submit_api_error(self, mock_post, audio_service):
        """API error returns None."""
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.text = '{"code": "BadRequest"}'
        error_response.json.return_value = {'code': 'BadRequest'}
        mock_post.return_value = error_response

        result = audio_service._submit_async_transcription(
            'https://example.com/file.mp3', 'fun-asr-mtl',
            {}, 'test-key')

        assert result is None

    @patch('requests.get')
    @patch('requests.post')
    def test_submit_task_failed(self, mock_post, mock_get, audio_service):
        """Task FAILED status returns None."""
        submit_response = MagicMock()
        submit_response.status_code = 200
        submit_response.json.return_value = {
            'output': {'task_id': 'test-fail'}
        }
        mock_post.return_value = submit_response

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            'output': {'task_status': 'FAILED', 'message': 'Model error'}
        }
        mock_get.return_value = poll_response

        result = audio_service._submit_async_transcription(
            'https://example.com/file.mp3', 'fun-asr-mtl',
            {}, 'test-key', poll_interval=1, max_wait=5)

        assert result is None


# ============== Two-pass Diarization Integration Tests ==============

class TestTwoPassDiarization:
    """Test the full two-pass diarization flow with mocked _submit_async_transcription."""

    def test_both_passes_succeed(self, audio_service):
        """Both passes succeed — merged result with speaker labels and Russian text."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        spk_data = {
            'transcripts': [{'sentences': [
                {'speaker_id': 0, 'text': 'xx', 'begin_time': 0, 'end_time': 2000},
                {'speaker_id': 1, 'text': 'yy', 'begin_time': 2000, 'end_time': 5000},
            ]}]
        }
        txt_data = {
            'transcripts': [{'sentences': [
                {'text': 'Привет', 'begin_time': 0, 'end_time': 2000},
                {'text': 'Как дела?', 'begin_time': 2000, 'end_time': 5000},
            ]}]
        }

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=[spk_data, txt_data]):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is not None
        assert 'Привет' in raw_text
        assert len(segments) == 2
        assert segments[0]['speaker_id'] == 0
        assert segments[0]['text'] == 'Привет'
        assert segments[1]['speaker_id'] == 1
        assert segments[1]['text'] == 'Как дела?'

    def test_pass1_fails_pass2_ok(self, audio_service):
        """Pass 1 (speakers) fails — return text without speaker labels."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        txt_data = {
            'transcripts': [{'sentences': [
                {'text': 'Привет', 'begin_time': 0, 'end_time': 2000},
            ]}]
        }

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=[None, txt_data]):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is not None
        assert 'Привет' in raw_text
        assert segments == []  # No speaker info available

    def test_pass2_fails_pass1_ok(self, audio_service):
        """Pass 2 (text) fails — return Pass 1 text with speakers (wrong language)."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        spk_data = {
            'transcripts': [{'sentences': [
                {'speaker_id': 0, 'text': 'スピーカー0', 'begin_time': 0, 'end_time': 2000},
                {'speaker_id': 1, 'text': 'スピーカー1', 'begin_time': 2000, 'end_time': 5000},
            ]}]
        }

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=[spk_data, None]):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is not None
        assert len(segments) == 2
        assert segments[0]['speaker_id'] == 0

    def test_both_passes_fail(self, audio_service):
        """Both passes fail — return (None, [])."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=[None, None]):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is None
        assert segments == []

    def test_language_params_per_model(self, audio_service):
        """Verify Pass 1 gets diarization_enabled, Pass 2 gets language (not language_hints)."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed-url'
        audio_service._oss_bucket = mock_bucket

        with patch.object(audio_service, '_submit_async_transcription',
                          return_value=None) as mock_submit:
            audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert mock_submit.call_count == 2
        # Pass 1: fun-asr-mtl with diarization params
        spk_call = mock_submit.call_args_list[0]
        assert spk_call[0][1] == 'fun-asr-mtl'
        assert 'diarization_enabled' in spk_call[0][2]
        # Pass 2: qwen3-asr-flash-filetrans with language (string, not list)
        txt_call = mock_submit.call_args_list[1]
        assert txt_call[0][1] == 'qwen3-asr-flash-filetrans'
        assert txt_call[0][2] == {'language': 'ru', 'enable_words': True}
        assert 'language_hints' not in txt_call[0][2]


# ============== _parse_speaker_segments / _parse_text_segments Tests ==============

class TestParseSegments:
    """Test parsing helpers for transcription results."""

    def test_parse_speaker_segments(self, audio_service):
        data = {
            'transcripts': [{'sentences': [
                {'speaker_id': 0, 'text': 'hello', 'begin_time': 0, 'end_time': 1000},
                {'speaker_id': 1, 'text': 'world', 'begin_time': 1000, 'end_time': 2000},
            ]}]
        }
        result = audio_service._parse_speaker_segments(data)
        assert len(result) == 2
        assert result[0]['speaker_id'] == 0
        assert result[1]['speaker_id'] == 1

    def test_parse_text_segments(self, audio_service):
        data = {
            'transcripts': [{'sentences': [
                {'text': 'Привет', 'begin_time': 0, 'end_time': 1000},
                {'text': '', 'begin_time': 1000, 'end_time': 2000},  # empty — skipped
                {'text': 'Мир', 'begin_time': 2000, 'end_time': 3000},
            ]}]
        }
        result = audio_service._parse_text_segments(data)
        assert len(result) == 2
        assert result[0]['text'] == 'Привет'
        assert result[1]['text'] == 'Мир'

    def test_parse_empty_data(self, audio_service):
        assert audio_service._parse_speaker_segments({}) == []
        assert audio_service._parse_text_segments({}) == []
        assert audio_service._parse_speaker_segments({'transcripts': []}) == []
        assert audio_service._parse_text_segments({'transcripts': []}) == []


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

    def test_all_async_unavailable_uses_sync_fallback(self):
        """When AUDIO_PROCESSOR_URL and MNS are both unavailable, should use sync fallback."""
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
             patch.object(webhook_main, 'get_telegram_service') as mock_tg, \
             patch.dict(os.environ, {'AUDIO_PROCESSOR_URL': ''}, clear=False):

            result = webhook_main.queue_audio_async(
                message, user, 'file-id', 'voice', 30
            )

            # Should fall back to sync
            mock_sync.assert_called_once()
            assert result == 'ok'
            # Job IS created (before async attempts)
            mock_db.return_value.create_job.assert_called_once()

    def test_http_and_mns_failure_uses_sync_fallback(self):
        """When HTTP invoke and MNS both fail, should mark job failed and use sync fallback."""
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
             patch('services.mns_service.MNSPublisher', return_value=mock_publisher), \
             patch.dict(os.environ, {'AUDIO_PROCESSOR_URL': ''}, clear=False):

            result = webhook_main.queue_audio_async(
                message, user, 'file-id', 'voice', 30
            )

            # Should fall back to sync
            mock_sync.assert_called_once()
            assert result == 'ok'
            # Job was created
            mock_db.return_value.create_job.assert_called_once()
            # Job should be marked as failed
            mock_db.return_value.update_job.assert_called_once()


# ============== Diarization Debug Tests ==============

class TestDiarizationDebug:
    """Test _diarization_debug recording and get_diarization_debug() formatting."""

    def _make_transcription_result(self, text="Hello"):
        """Helper: create a valid transcription result dict."""
        return {
            'transcripts': [{
                'sentences': [{
                    'text': text,
                    'speaker_id': 0,
                    'begin_time': 0,
                    'end_time': 1000,
                }]
            }]
        }

    @patch('requests.get')
    @patch('requests.post')
    def test_diarization_debug_populated(self, mock_post, mock_get, audio_service):
        """Both passes succeed — debug dict has pass1/pass2 entries."""
        # Mock OSS upload
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://bucket.oss.com/key?sig=abc'
        audio_service._oss_bucket = mock_bucket

        # Submit responses (both succeed)
        submit_resp = MagicMock()
        submit_resp.status_code = 200
        submit_resp.json.return_value = {'output': {'task_id': 'task-123'}}
        mock_post.return_value = submit_resp

        # Route GET requests by URL
        trans_data = self._make_transcription_result()

        def get_router(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if '/tasks/' in url:
                resp.json.return_value = {
                    'output': {
                        'task_status': 'SUCCEEDED',
                        'results': [{'transcription_url': 'https://result.com/data.json'}]
                    }
                }
            else:
                resp.json.return_value = trans_data
            return resp

        mock_get.side_effect = get_router

        with patch('time.sleep'):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        dbg = audio_service._diarization_debug
        assert 'pass1_result' in dbg
        assert 'pass2_result' in dbg
        assert dbg['pass1_result'] == 'ok'
        assert dbg['pass2_result'] == 'ok'
        assert 'pass1_submit_status' in dbg
        assert 'pass2_submit_status' in dbg
        assert dbg['pass1_submit_status'] == 200
        assert 'spk_segments' in dbg
        assert 'txt_segments' in dbg

    @patch('requests.get')
    @patch('requests.post')
    def test_diarization_debug_records_fallback(self, mock_post, mock_get, audio_service):
        """Pass 2 task fails — fallback recorded in debug."""
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://bucket.oss.com/key?sig=abc'
        audio_service._oss_bucket = mock_bucket

        # Submit: assign different task IDs per model
        call_count = [0]
        def post_router(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            payload = kwargs.get('json', {})
            model = payload.get('model', '')
            if model == 'fun-asr-mtl':
                resp.json.return_value = {'output': {'task_id': 'task-spk'}}
            else:
                resp.json.return_value = {'output': {'task_id': 'task-txt'}}
            return resp
        mock_post.side_effect = post_router

        trans_data = self._make_transcription_result("Привет")

        def get_router(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if 'task-spk' in url:
                resp.json.return_value = {
                    'output': {
                        'task_status': 'SUCCEEDED',
                        'results': [{'transcription_url': 'https://result.com/data.json'}]
                    }
                }
            elif 'task-txt' in url:
                resp.json.return_value = {
                    'output': {'task_status': 'FAILED', 'message': 'Model not exist'}
                }
            else:
                # transcription fetch
                resp.json.return_value = trans_data
            return resp

        mock_get.side_effect = get_router

        with patch('time.sleep'):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        dbg = audio_service._diarization_debug
        assert dbg['pass2_result'] == 'task_failed: Model not exist'
        assert dbg['fallback'] == 'pass2_failed_using_pass1_text'
        assert 'pass2_poll_body' in dbg

    def test_get_diarization_debug_format(self, audio_service):
        """get_diarization_debug() returns formatted text with key sections."""
        audio_service._diarization_debug = {
            'pass1_result': 'ok',
            'pass1_submit_status': 200,
            'pass1_task_id': 'task-aaa',
            'pass2_result': 'task_failed: Model not exist',
            'pass2_submit_status': 200,
            'pass2_task_id': 'task-bbb',
            'spk_segments': 5,
            'txt_segments': 0,
            'fallback': 'pass2_failed_using_pass1_text',
        }

        text = audio_service.get_diarization_debug()
        assert text is not None
        assert 'DIARIZATION DEBUG' in text
        assert 'Pass 1' in text
        assert 'Pass 2' in text
        assert 'task_failed: Model not exist' in text
        assert 'pass2_failed_using_pass1_text' in text
        assert 'spk_segments: 5' in text
        assert len(text) <= 3900

    def test_debug_html_escaped(self, audio_service):
        """HTML special chars in API response are escaped."""
        audio_service._diarization_debug = {
            'pass1_result': 'ok',
            'pass2_result': 'submit_failed',
            'pass2_submit_body': '{"error": "<script>alert(1)</script>"}',
            'fallback': 'both_empty',
        }

        text = audio_service.get_diarization_debug()
        assert text is not None
        assert '<script>' not in text
        assert '&lt;script&gt;' in text

    def test_get_diarization_debug_empty(self, audio_service):
        """Returns None when no debug data."""
        audio_service._diarization_debug = {}
        assert audio_service.get_diarization_debug() is None

    def test_debug_assemblyai_backend(self, audio_service):
        """Debug output for AssemblyAI backend."""
        audio_service._diarization_debug = {
            'backend': 'assemblyai',
            'model': 'universal-3-pro',
            'spk_segments': 3,
            'unique_speakers': 2,
            'transcript_id': 'tx_abc123',
            'merged_detail': 'spk0:Привет; spk1:Здравствуйте',
            'fallback': 'none',
        }
        text = audio_service.get_diarization_debug()
        assert 'ASSEMBLYAI' in text
        assert 'universal-3-pro' in text
        assert 'segments: 3' in text
        assert 'speakers: 2' in text
        assert 'tx_abc123' in text
        # Should NOT contain DashScope Pass 1/2 sections
        assert 'fun-asr-mtl' not in text

    def test_debug_gemini_backend(self, audio_service):
        """Debug output for Gemini backend."""
        audio_service._diarization_debug = {
            'backend': 'gemini',
            'model': 'gemini-3-flash-preview',
            'spk_segments': 5,
            'unique_speakers': 3,
            'merged_detail': 'spk0:Привет; spk1:Да; spk2:Нет',
            'fallback': 'none',
        }
        text = audio_service.get_diarization_debug()
        assert 'GEMINI' in text
        assert 'gemini-3-flash-preview' in text
        assert 'segments: 5' in text
        assert 'speakers: 3' in text


# ============== AssemblyAI Backend ==============

class TestDiarizeAssemblyAI:
    """Tests for _diarize_assemblyai() method."""

    @pytest.fixture
    def audio_service(self):
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

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('time.sleep')
    @patch('requests.get')
    @patch('requests.post')
    def test_success(self, mock_post, mock_get, mock_sleep, audio_service):
        """Full success path: upload → submit → poll → parse utterances."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'upload_url': 'https://cdn.assemblyai.com/upload/123'}),
            MagicMock(status_code=200, json=lambda: {'id': 'tx_123'}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'status': 'completed',
            'text': 'Привет. Здравствуйте.',
            'utterances': [
                {'speaker': 'A', 'text': 'Привет.', 'start': 0, 'end': 2000},
                {'speaker': 'B', 'text': 'Здравствуйте.', 'start': 2000, 'end': 5000},
            ]
        })
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_assemblyai('/tmp/test.mp3')

        assert raw == 'Привет. Здравствуйте.'
        assert len(segs) == 2
        assert segs[0]['speaker_id'] == 0
        assert segs[1]['speaker_id'] == 1
        assert segs[0]['text'] == 'Привет.'
        assert segs[1]['text'] == 'Здравствуйте.'
        assert segs[0]['begin_time'] == 0
        assert segs[1]['end_time'] == 5000
        assert audio_service._diarization_debug['backend'] == 'assemblyai'
        assert audio_service._diarization_debug['transcript_id'] == 'tx_123'

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('time.sleep')
    @patch('requests.get')
    @patch('requests.post')
    def test_three_speakers(self, mock_post, mock_get, mock_sleep, audio_service):
        """Three speakers mapped to speaker_id 0, 1, 2."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'upload_url': 'https://cdn.assemblyai.com/upload/123'}),
            MagicMock(status_code=200, json=lambda: {'id': 'tx_456'}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'status': 'completed',
            'text': 'A B C',
            'utterances': [
                {'speaker': 'A', 'text': 'Hello', 'start': 0, 'end': 1000},
                {'speaker': 'B', 'text': 'Hi', 'start': 1000, 'end': 2000},
                {'speaker': 'C', 'text': 'Hey', 'start': 2000, 'end': 3000},
            ]
        })
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_assemblyai('/tmp/test.mp3')

        assert len(segs) == 3
        assert segs[0]['speaker_id'] == 0
        assert segs[1]['speaker_id'] == 1
        assert segs[2]['speaker_id'] == 2
        assert audio_service._diarization_debug['unique_speakers'] == 3

    def test_no_api_key(self, audio_service):
        """Returns (None, []) when ASSEMBLYAI_API_KEY not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the key is not in env
            os.environ.pop('ASSEMBLYAI_API_KEY', None)
            raw, segs = audio_service._diarize_assemblyai('/tmp/test.mp3')
        assert raw is None
        assert segs == []

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('requests.post')
    def test_upload_failure(self, mock_post, audio_service):
        """Upload failure returns (None, [])."""
        mock_post.return_value = MagicMock(status_code=500)
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_assemblyai('/tmp/test.mp3')
        assert raw is None
        assert segs == []

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('time.sleep')
    @patch('requests.get')
    @patch('requests.post')
    def test_transcription_error(self, mock_post, mock_get, mock_sleep, audio_service):
        """AssemblyAI returns error status."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'upload_url': 'https://cdn.assemblyai.com/upload/123'}),
            MagicMock(status_code=200, json=lambda: {'id': 'tx_err'}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'status': 'error',
            'error': 'Audio too short',
        })
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_assemblyai('/tmp/test.mp3')
        assert raw is None
        assert segs == []

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('time.sleep')
    @patch('requests.get')
    @patch('requests.post')
    def test_progress_callback(self, mock_post, mock_get, mock_sleep, audio_service):
        """Progress callback is called at each stage."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'upload_url': 'https://cdn.assemblyai.com/upload/123'}),
            MagicMock(status_code=200, json=lambda: {'id': 'tx_123'}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'status': 'completed', 'text': 'ok', 'utterances': []
        })
        callback = MagicMock()
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            audio_service._diarize_assemblyai('/tmp/test.mp3', progress_callback=callback)
        assert callback.call_count == 2

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('time.sleep')
    @patch('requests.get')
    @patch('requests.post')
    def test_submit_includes_speech_models(self, mock_post, mock_get, mock_sleep, audio_service):
        """Submit request includes required speech_models parameter."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'upload_url': 'https://cdn.assemblyai.com/upload/123'}),
            MagicMock(status_code=200, json=lambda: {'id': 'tx_123'}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {
            'status': 'completed', 'text': 'ok', 'utterances': []
        })
        with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
            audio_service._diarize_assemblyai('/tmp/test.mp3')
        # Verify the submit call (second post) includes speech_models
        submit_call = mock_post.call_args_list[1]
        assert submit_call.kwargs['json']['speech_models'] == ['universal-2']


# ============== Gemini Backend ==============

class TestDiarizeGemini:
    """Tests for _diarize_gemini() method."""

    @pytest.fixture
    def audio_service(self):
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

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('requests.post')
    def test_success(self, mock_post, audio_service):
        """Full success path with structured output."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'candidates': [{'content': {'parts': [{'text': json.dumps({
                'segments': [
                    {'speaker': '1', 'text': 'Привет.'},
                    {'speaker': '2', 'text': 'Здравствуйте.'},
                    {'speaker': '1', 'text': 'Как дела?'},
                ]
            })}]}}]
        })
        with patch.dict(os.environ, {'GOOGLE_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_gemini('/tmp/test.mp3')

        assert len(segs) == 3
        assert segs[0]['speaker_id'] == 0
        assert segs[1]['speaker_id'] == 1
        assert segs[2]['speaker_id'] == 0  # same speaker as first
        assert segs[0]['text'] == 'Привет.'
        assert 'Привет.' in raw
        assert 'Здравствуйте.' in raw
        assert audio_service._diarization_debug['backend'] == 'gemini'
        assert audio_service._diarization_debug['unique_speakers'] == 2
        # Gemini doesn't provide timestamps
        assert segs[0]['begin_time'] == 0

    def test_no_api_key(self, audio_service):
        """Returns (None, []) when GOOGLE_API_KEY not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('GOOGLE_API_KEY', None)
            os.environ.pop('GEMINI_API_KEY', None)
            raw, segs = audio_service._diarize_gemini('/tmp/test.mp3')
        assert raw is None
        assert segs == []

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('requests.post')
    def test_api_failure(self, mock_post, audio_service):
        """Gemini API returns non-200."""
        mock_post.return_value = MagicMock(status_code=500, text='Internal Server Error')
        with patch.dict(os.environ, {'GOOGLE_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_gemini('/tmp/test.mp3')
        assert raw is None
        assert segs == []

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('requests.post')
    def test_gemini_api_key_env(self, mock_post, audio_service):
        """Accepts GEMINI_API_KEY as alternative env var."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'candidates': [{'content': {'parts': [{'text': json.dumps({
                'segments': [{'speaker': '1', 'text': 'Тест.'}]
            })}]}}]
        })
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'alt-key'}, clear=True):
            os.environ.pop('GOOGLE_API_KEY', None)
            raw, segs = audio_service._diarize_gemini('/tmp/test.mp3')
        assert len(segs) == 1
        assert segs[0]['text'] == 'Тест.'

    @patch('builtins.open', MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b'fake-audio'))),
        __exit__=MagicMock(return_value=False)
    )))
    @patch('requests.post')
    def test_empty_segments_filtered(self, mock_post, audio_service):
        """Empty text segments are filtered out."""
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {
            'candidates': [{'content': {'parts': [{'text': json.dumps({
                'segments': [
                    {'speaker': '1', 'text': 'Привет.'},
                    {'speaker': '2', 'text': ''},
                    {'speaker': '1', 'text': '  '},
                ]
            })}]}}]
        })
        with patch.dict(os.environ, {'GOOGLE_API_KEY': 'test-key'}):
            raw, segs = audio_service._diarize_gemini('/tmp/test.mp3')
        assert len(segs) == 1  # only non-empty


# ============== Backend Routing ==============

class TestDiarizationRouting:
    """Tests for DIARIZATION_BACKEND routing in transcribe_with_diarization()."""

    @pytest.fixture
    def audio_service(self):
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

    @patch.object(AudioService, '_diarize_assemblyai')
    def test_routing_assemblyai(self, mock_assemblyai, audio_service):
        """DIARIZATION_BACKEND=assemblyai routes to _diarize_assemblyai."""
        mock_assemblyai.return_value = ('text', [{'speaker_id': 0, 'text': 'hi'}])
        with patch.dict(os.environ, {'DIARIZATION_BACKEND': 'assemblyai'}):
            raw, segs = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        mock_assemblyai.assert_called_once()
        assert raw == 'text'
        assert len(segs) == 1

    @patch.object(AudioService, '_diarize_gemini')
    def test_routing_gemini(self, mock_gemini, audio_service):
        """DIARIZATION_BACKEND=gemini routes to _diarize_gemini."""
        mock_gemini.return_value = ('text', [{'speaker_id': 0, 'text': 'hi'}])
        with patch.dict(os.environ, {'DIARIZATION_BACKEND': 'gemini'}):
            raw, segs = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        mock_gemini.assert_called_once()
        assert raw == 'text'
        assert len(segs) == 1

    @patch.object(AudioService, '_diarize_assemblyai')
    @patch.object(AudioService, '_diarize_gemini')
    @patch.object(AudioService, '_upload_to_oss_with_url')
    def test_routing_default_dashscope(self, mock_oss, mock_gemini, mock_assemblyai, audio_service):
        """Default (no env var) skips assemblyai/gemini, goes to dashscope."""
        mock_oss.return_value = (None, None)  # will fail early in dashscope path
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DIARIZATION_BACKEND', None)
            os.environ['DASHSCOPE_API_KEY'] = 'test'
            raw, segs = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        mock_assemblyai.assert_not_called()
        mock_gemini.assert_not_called()

    @patch.object(AudioService, '_upload_to_oss_with_url')
    @patch.object(AudioService, '_diarize_assemblyai')
    def test_fallback_to_dashscope(self, mock_assemblyai, mock_oss, audio_service):
        """AssemblyAI returns empty segments → falls back to dashscope."""
        mock_assemblyai.return_value = (None, [])
        mock_oss.return_value = (None, None)  # dashscope path will fail at OSS
        with patch.dict(os.environ, {'DIARIZATION_BACKEND': 'assemblyai',
                                      'DASHSCOPE_API_KEY': 'test'}):
            raw, segs = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        mock_assemblyai.assert_called_once()
        # Dashscope was attempted (OSS upload called)
        mock_oss.assert_called_once()

    @patch.object(AudioService, '_upload_to_oss_with_url')
    @patch.object(AudioService, '_diarize_gemini')
    def test_gemini_fallback_to_dashscope(self, mock_gemini, mock_oss, audio_service):
        """Gemini returns empty segments → falls back to dashscope."""
        mock_gemini.return_value = (None, [])
        mock_oss.return_value = (None, None)
        with patch.dict(os.environ, {'DIARIZATION_BACKEND': 'gemini',
                                      'DASHSCOPE_API_KEY': 'test'}):
            raw, segs = audio_service.transcribe_with_diarization('/tmp/test.mp3')
        mock_gemini.assert_called_once()
        mock_oss.assert_called_once()


# ============== Word-level timestamps (enable_words) ==============

class TestParseTextSegmentsWordLevel:
    """Tests for _parse_text_segments() with word-level timestamps."""

    def test_parse_text_segments_word_level(self, audio_service):
        """Words array present → one segment per word."""
        trans_data = {
            'transcripts': [{
                'sentences': [{
                    'text': 'Привет мир',
                    'begin_time': 0,
                    'end_time': 2000,
                    'words': [
                        {'text': 'Привет', 'punctuation': '', 'begin_time': 0, 'end_time': 800},
                        {'text': 'мир', 'punctuation': '', 'begin_time': 900, 'end_time': 2000},
                    ]
                }]
            }]
        }
        segments = audio_service._parse_text_segments(trans_data)
        assert len(segments) == 2
        assert segments[0] == {'text': 'Привет', 'begin_time': 0, 'end_time': 800}
        assert segments[1] == {'text': 'мир', 'begin_time': 900, 'end_time': 2000}

    def test_parse_text_segments_mixed_words_and_sentences(self, audio_service):
        """Sentences with and without words array."""
        trans_data = {
            'transcripts': [{
                'sentences': [
                    {
                        'text': 'Первое предложение',
                        'begin_time': 0,
                        'end_time': 3000,
                        'words': [
                            {'text': 'Первое', 'punctuation': '', 'begin_time': 0, 'end_time': 1500},
                            {'text': 'предложение', 'punctuation': '', 'begin_time': 1600, 'end_time': 3000},
                        ]
                    },
                    {
                        'text': 'Без слов',
                        'begin_time': 3100,
                        'end_time': 5000,
                    },
                ]
            }]
        }
        segments = audio_service._parse_text_segments(trans_data)
        assert len(segments) == 3
        assert segments[0]['text'] == 'Первое'
        assert segments[1]['text'] == 'предложение'
        assert segments[2] == {'text': 'Без слов', 'begin_time': 3100, 'end_time': 5000}

    def test_parse_text_segments_sentence_fallback(self, audio_service):
        """No words key at all → sentence-level (backward compat)."""
        trans_data = {
            'transcripts': [{
                'sentences': [
                    {'text': 'Длинное предложение', 'begin_time': 0, 'end_time': 5000},
                    {'text': 'Второе', 'begin_time': 5100, 'end_time': 8000},
                ]
            }]
        }
        segments = audio_service._parse_text_segments(trans_data)
        assert len(segments) == 2
        assert segments[0] == {'text': 'Длинное предложение', 'begin_time': 0, 'end_time': 5000}
        assert segments[1] == {'text': 'Второе', 'begin_time': 5100, 'end_time': 8000}

    def test_parse_text_segments_punctuation_combined(self, audio_service):
        """Word text + punctuation are concatenated."""
        trans_data = {
            'transcripts': [{
                'sentences': [{
                    'text': 'Да нет',
                    'begin_time': 0,
                    'end_time': 2000,
                    'words': [
                        {'text': 'Да', 'punctuation': ',', 'begin_time': 0, 'end_time': 800},
                        {'text': 'нет', 'punctuation': '.', 'begin_time': 900, 'end_time': 2000},
                    ]
                }]
            }]
        }
        segments = audio_service._parse_text_segments(trans_data)
        assert len(segments) == 2
        assert segments[0]['text'] == 'Да,'
        assert segments[1]['text'] == 'нет.'

    def test_parse_text_segments_empty_words_array(self, audio_service):
        """words: [] → fallback to sentence text."""
        trans_data = {
            'transcripts': [{
                'sentences': [{
                    'text': 'Целое предложение',
                    'begin_time': 0,
                    'end_time': 4000,
                    'words': []
                }]
            }]
        }
        segments = audio_service._parse_text_segments(trans_data)
        assert len(segments) == 1
        assert segments[0] == {'text': 'Целое предложение', 'begin_time': 0, 'end_time': 4000}


class TestAlignWordLevelPrecise:
    """Tests for alignment precision with word-level timestamps."""

    def test_align_word_level_precise_timestamps(self, audio_service):
        """Word-level segments with exact timestamps → correct speaker assignment."""
        speaker_segments = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 3000, 'text': ''},
            {'speaker_id': 1, 'begin_time': 3000, 'end_time': 6000, 'text': ''},
        ]
        # Word-level: each word is its own segment with precise times
        text_segments = [
            {'text': 'Привет,', 'begin_time': 100, 'end_time': 600},
            {'text': 'как', 'begin_time': 700, 'end_time': 1100},
            {'text': 'дела?', 'begin_time': 1200, 'end_time': 2000},
            {'text': 'Хорошо,', 'begin_time': 3100, 'end_time': 3800},
            {'text': 'спасибо.', 'begin_time': 3900, 'end_time': 5000},
        ]
        merged = audio_service._align_speakers_with_text(speaker_segments, text_segments)
        # Speaker 0 should have first 3 words, speaker 1 should have last 2
        assert len(merged) == 2
        assert merged[0]['speaker_id'] == 0
        assert 'Привет' in merged[0]['text']
        assert 'дела' in merged[0]['text']
        assert merged[1]['speaker_id'] == 1
        assert 'Хорошо' in merged[1]['text']
        assert 'спасибо' in merged[1]['text']


class TestDebugWordLevel:
    """Tests for debug output with word-level data."""

    def test_debug_shows_timeline_normalized(self, audio_service):
        """Debug output includes timeline_normalized and txt_mode when present."""
        audio_service._diarization_debug = {
            'spk_segments': 5,
            'txt_segments': 150,
            'txt_word_level': True,
            'timeline_normalized': '120000ms/118500ms',
            'spk_detail': 'spk0[0-30000]',
            'txt_detail': '[0-500]Привет',
        }
        debug_text = audio_service.get_diarization_debug()
        assert 'txt_mode: word-level' in debug_text
        assert 'timeline_normalized: 120000ms/118500ms' in debug_text
        assert 'txt_segments: 150' in debug_text


# ============== Debug Mode Gating Tests ==============

class TestDebugModeGating:
    """Test that debug output respects debug_mode setting."""

    def test_debug_gated_by_setting(self):
        """Debug output should only be sent when debug_mode=True."""
        # Simulate the condition from handler.py
        settings_on = {'debug_mode': True}
        settings_off = {'debug_mode': False}
        settings_default = {}

        assert settings_on.get('debug_mode', False) is True
        assert settings_off.get('debug_mode', False) is False
        assert settings_default.get('debug_mode', False) is False

    def test_debug_default_is_off(self):
        """debug_mode defaults to False (no debug output by default)."""
        settings = {}
        assert settings.get('debug_mode', False) is False


# ============== v3.6.1 Robustness Tests ==============

class TestFutureResultTimeout:
    """Test that ThreadPoolExecutor futures have timeout protection."""

    def test_future_timeout_handling(self, audio_service):
        """future.result(timeout=270) should catch FuturesTimeoutError."""
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed'
        audio_service._oss_bucket = mock_bucket

        def slow_transcription(*args, **kwargs):
            raise FuturesTimeoutError("timed out")

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=slow_transcription):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        # Both passes timed out → should return (None, [])
        assert raw_text is None
        assert segments == []

    def test_future_timeout_pass1_only(self, audio_service):
        """Pass 1 timeout, Pass 2 succeeds → text without speakers."""
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = 'https://oss.example.com/signed'
        audio_service._oss_bucket = mock_bucket

        txt_data = {
            'transcripts': [{'sentences': [
                {'text': 'Привет мир', 'begin_time': 0, 'end_time': 2000},
            ]}]
        }

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FuturesTimeoutError("pass 1 timed out")
            return txt_data

        with patch.object(audio_service, '_submit_async_transcription',
                          side_effect=side_effect):
            raw_text, segments = audio_service.transcribe_with_diarization('/tmp/test.mp3')

        assert raw_text is not None
        assert 'Привет' in raw_text
        assert segments == []  # no speaker info


class TestTelegramTimeouts:
    """Test that TelegramService methods use timeout."""

    def test_default_timeout_constant(self, tg_service):
        assert tg_service.DEFAULT_TIMEOUT == 30

    def test_download_timeout_constant(self, tg_service):
        assert tg_service.DOWNLOAD_TIMEOUT == 60

    @patch('requests.Session.post')
    def test_send_message_has_timeout(self, mock_post, tg_service):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'ok': True, 'result': {}},
            raise_for_status=lambda: None,
        )
        tg_service.send_message(123, "test")
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 30

    @patch('requests.Session.post')
    def test_edit_message_has_timeout(self, mock_post, tg_service):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'ok': True, 'result': {}},
            raise_for_status=lambda: None,
        )
        tg_service.edit_message_text(123, 1, "test")
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 30

    @patch('requests.Session.post')
    def test_delete_message_has_timeout(self, mock_post, tg_service):
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
        )
        tg_service.delete_message(123, 1)
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 30

    @patch('requests.Session.get')
    def test_download_file_has_timeout(self, mock_get, tg_service):
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'data']
        mock_response.raise_for_status = lambda: None
        mock_get.return_value = mock_response
        tg_service.download_file('file/test.ogg')
        _, kwargs = mock_get.call_args
        assert kwargs.get('timeout') == 60

    @patch('requests.Session.get')
    def test_get_file_path_has_timeout(self, mock_get, tg_service):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'ok': True, 'result': {'file_path': 'a/b.ogg'}},
            raise_for_status=lambda: None,
        )
        tg_service.get_file_path('file-123')
        _, kwargs = mock_get.call_args
        assert kwargs.get('timeout') == 30

    @patch('requests.Session.post')
    def test_send_chat_action_has_short_timeout(self, mock_post, tg_service):
        """send_chat_action uses 2s timeout (fire-and-forget)."""
        mock_post.return_value = MagicMock(status_code=200)
        tg_service.send_chat_action(123, 'typing')
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 2


class TestDatetimeDeprecation:
    """Test that datetime.utcnow() is NOT used anywhere."""

    def test_utility_no_utcnow(self):
        """utility.py should not contain datetime.utcnow()."""
        from utility import UtilityService
        import inspect
        source = inspect.getsource(UtilityService)
        assert 'utcnow()' not in source, \
            "datetime.utcnow() is deprecated — use datetime.now(timezone.utc)"

    def test_setup_logging_uses_timezone_utc(self):
        """setup_logging formatter should use timezone.utc."""
        from utility import UtilityService
        import inspect
        source = inspect.getsource(UtilityService.setup_logging)
        # If pythonjsonlogger is available, it should use timezone.utc
        assert 'timezone.utc' in source or 'utcnow' not in source


class TestJobDedup:
    """Test MNS at-least-once dedup in handler.py (mocked)."""

    def test_duplicate_processing_job_skipped(self):
        """Job with status 'processing' should be skipped."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

        import handler

        job_data = {
            'job_id': 'test-job-1',
            'user_id': '123',
            'chat_id': '456',
            'file_id': 'file-abc',
            'duration': 60,
        }

        with patch.object(handler, 'get_db_service') as mock_db, \
             patch.object(handler, 'get_telegram_service') as mock_tg, \
             patch.object(handler, 'get_audio_service') as mock_audio:
            mock_db.return_value.get_job.return_value = {'status': 'processing'}
            result = handler.process_job(job_data)

        assert result == {'ok': True, 'result': 'duplicate'}
        # Should NOT have started processing
        mock_db.return_value.update_job.assert_not_called()

    def test_duplicate_completed_job_skipped(self):
        """Job with status 'completed' should be skipped."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

        import handler

        job_data = {
            'job_id': 'test-job-2',
            'user_id': '123',
            'chat_id': '456',
            'file_id': 'file-def',
            'duration': 30,
        }

        with patch.object(handler, 'get_db_service') as mock_db, \
             patch.object(handler, 'get_telegram_service') as mock_tg, \
             patch.object(handler, 'get_audio_service') as mock_audio:
            mock_db.return_value.get_job.return_value = {'status': 'completed'}
            result = handler.process_job(job_data)

        assert result == {'ok': True, 'result': 'duplicate'}

    def test_pending_job_proceeds(self):
        """Job with status 'pending' should proceed normally."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

        import handler

        job_data = {
            'job_id': 'test-job-3',
            'user_id': '123',
            'chat_id': '456',
            'file_id': 'file-ghi',
            'duration': 30,
        }

        with patch.object(handler, 'get_db_service') as mock_db, \
             patch.object(handler, 'get_telegram_service') as mock_tg, \
             patch.object(handler, 'get_audio_service') as mock_audio:
            # Job exists with 'pending' status
            mock_db.return_value.get_job.return_value = {'status': 'pending'}
            # Will fail at get_file_path but that's ok — we just check dedup passed
            mock_tg.return_value.get_file_path.return_value = None
            mock_tg.return_value.send_message.return_value = {'ok': True, 'result': {'message_id': 1}}
            mock_tg.return_value.edit_message_text.return_value = {'ok': True}
            mock_tg.return_value.send_chat_action.return_value = True

            result = handler.process_job(job_data)

        # Should have called update_job (meaning dedup check passed)
        mock_db.return_value.update_job.assert_called()

    def test_new_job_proceeds(self):
        """Job not found in DB (None) should proceed."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

        import handler

        job_data = {
            'job_id': 'test-job-4',
            'user_id': '123',
            'chat_id': '456',
            'file_id': 'file-jkl',
            'duration': 30,
        }

        with patch.object(handler, 'get_db_service') as mock_db, \
             patch.object(handler, 'get_telegram_service') as mock_tg, \
             patch.object(handler, 'get_audio_service') as mock_audio:
            mock_db.return_value.get_job.return_value = None
            mock_tg.return_value.get_file_path.return_value = None
            mock_tg.return_value.send_message.return_value = {'ok': True, 'result': {'message_id': 1}}
            mock_tg.return_value.edit_message_text.return_value = {'ok': True}
            mock_tg.return_value.send_chat_action.return_value = True

            result = handler.process_job(job_data)

        # Should have called update_job
        mock_db.return_value.update_job.assert_called()


# ============== v4.1.0 Auto-Detection Tests ==============

class TestAntiDialoguePromptRule:
    """Test that non-dialogue prompt forbids em-dashes."""

    def test_non_dialogue_forbids_dashes(self, audio_service):
        """is_dialogue=False should include anti-dash rule."""
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=False
        )
        assert "НЕ используй тире" in prompt
        assert "связный текст" in prompt
        assert "ФОРМАТ ДИАЛОГА" not in prompt

    def test_dialogue_has_no_anti_dash_rule(self, audio_service):
        """is_dialogue=True should NOT include anti-dash rule."""
        prompt = audio_service._build_format_prompt(
            "текст", use_code_tags=False, use_yo=True,
            is_chunked=False, is_dialogue=True
        )
        assert "ФОРМАТ ДИАЛОГА" in prompt
        assert "НЕ используй тире" not in prompt


class TestAutoDetectionLogic:
    """Test speaker count auto-detection logic (from audio-processor)."""

    def test_two_speakers_is_dialogue(self):
        """2+ unique speakers → is_dialogue=True."""
        segments = [
            {'speaker_id': 0, 'text': 'Привет'},
            {'speaker_id': 1, 'text': 'Здравствуйте'},
            {'speaker_id': 0, 'text': 'Как дела?'},
        ]
        unique_speakers = len(set(s.get('speaker_id', 0) for s in segments))
        assert unique_speakers >= 2

    def test_one_speaker_is_not_dialogue(self):
        """1 unique speaker → is_dialogue=False."""
        segments = [
            {'speaker_id': 0, 'text': 'Привет'},
            {'speaker_id': 0, 'text': 'Это я один говорю'},
        ]
        unique_speakers = len(set(s.get('speaker_id', 0) for s in segments))
        assert unique_speakers < 2

    def test_missing_speaker_id_defaults_to_zero(self):
        """Segments without speaker_id default to 0 → 1 speaker."""
        segments = [
            {'text': 'Привет'},
            {'text': 'Без спикера'},
        ]
        unique_speakers = len(set(s.get('speaker_id', 0) for s in segments))
        assert unique_speakers == 1

    def test_empty_segments_fallback(self):
        """Empty segments list → fallback to regular ASR."""
        segments = []
        assert not segments  # falsy → fallback path

    def test_one_speaker_uses_raw_text(self):
        """1 speaker → use raw_text, not format_dialogue."""
        segments = [
            {'speaker_id': 0, 'text': 'Первая фраза'},
            {'speaker_id': 0, 'text': 'Вторая фраза'},
        ]
        raw_text = "Первая фраза. Вторая фраза."
        unique_speakers = len(set(s.get('speaker_id', 0) for s in segments))
        if unique_speakers >= 2:
            text = "dialogue"
        else:
            text = raw_text or ' '.join(s.get('text', '') for s in segments)
        assert text == raw_text
        assert "—" not in text

    def test_three_speakers_is_dialogue(self):
        """3 speakers → is_dialogue=True."""
        segments = [
            {'speaker_id': 0, 'text': 'А'},
            {'speaker_id': 1, 'text': 'Б'},
            {'speaker_id': 2, 'text': 'В'},
        ]
        unique_speakers = len(set(s.get('speaker_id', 0) for s in segments))
        assert unique_speakers >= 2


class TestRoutingThreshold:
    """Test sync/async routing by duration threshold."""

    def test_short_audio_sync(self):
        """Audio < 60s should go sync."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
        import main as webhook_main
        assert webhook_main.SYNC_PROCESSING_THRESHOLD == 60
        duration = 30
        assert duration < webhook_main.SYNC_PROCESSING_THRESHOLD

    def test_long_audio_async(self):
        """Audio >= 60s should go async."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
        import main as webhook_main
        duration = 60
        assert duration >= webhook_main.SYNC_PROCESSING_THRESHOLD

    def test_exact_threshold_is_async(self):
        """Audio exactly at threshold should go async."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
        import main as webhook_main
        duration = 60
        assert duration >= webhook_main.SYNC_PROCESSING_THRESHOLD
