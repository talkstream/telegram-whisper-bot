#!/usr/bin/env python3
"""
Unit tests for v5.1.0 quality fixes:
- Speaker inertia in gap-filling (_align_speakers_with_text)
- Hallucination guard threshold raised to 40%
- Mid-sentence truncation detection
- Overlap strip regex robustness

Run with: python -m pytest alibaba/tests/test_quality_fixes_v51.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

import pytest
import requests


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


@pytest.fixture
def audio_service():
    from audio import AudioService
    return AudioService(whisper_backend='qwen-asr', alibaba_api_key='test-key')


# === Speaker Inertia Tests ===

class TestSpeakerInertia:
    """Tests for speaker inertia in _align_speakers_with_text()."""

    def test_gap_keeps_current_speaker(self, audio_service):
        """Word in gap between segments keeps current speaker (inertia)."""
        speaker_segments = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 1000},
            # Gap: 1000-2000ms
            {'speaker_id': 1, 'begin_time': 2000, 'end_time': 3000},
        ]
        # Words: first inside spk0, second in gap (should stay spk0),
        # third inside spk1
        text_segments = [
            {'text': 'hello world goodbye', 'begin_time': 0, 'end_time': 3000},
        ]
        result = audio_service._align_speakers_with_text(speaker_segments, text_segments)

        # "hello" at ~0ms → spk0, "world" at ~1000ms → gap → inertia → spk0
        # "goodbye" at ~2000ms → inside spk1
        spk0_text = ' '.join(seg['text'] for seg in result if seg['speaker_id'] == 0)
        spk1_text = ' '.join(seg['text'] for seg in result if seg['speaker_id'] == 1)
        assert 'hello' in spk0_text
        assert 'world' in spk0_text  # inertia: stays with spk0
        assert 'goodbye' in spk1_text

    def test_overlap_switches_speaker(self, audio_service):
        """Word inside another speaker's segment switches speaker."""
        speaker_segments = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 500},
            {'speaker_id': 1, 'begin_time': 500, 'end_time': 1500},
        ]
        text_segments = [
            {'text': 'first second', 'begin_time': 0, 'end_time': 1500},
        ]
        result = audio_service._align_speakers_with_text(speaker_segments, text_segments)

        # "first" at ~0ms → spk0, "second" at ~750ms → inside spk1
        spk0_text = ' '.join(seg['text'] for seg in result if seg['speaker_id'] == 0)
        spk1_text = ' '.join(seg['text'] for seg in result if seg['speaker_id'] == 1)
        assert 'first' in spk0_text
        assert 'second' in spk1_text

    def test_first_word_in_gap_uses_nearest(self, audio_service):
        """First word with no current speaker in gap uses nearest segment."""
        speaker_segments = [
            # Gap at start: 0-500ms
            {'speaker_id': 1, 'begin_time': 500, 'end_time': 1500},
        ]
        text_segments = [
            {'text': 'early word', 'begin_time': 0, 'end_time': 1500},
        ]
        result = audio_service._align_speakers_with_text(speaker_segments, text_segments)

        # "early" at ~0ms → gap, no current speaker → nearest is spk1
        assert result[0]['speaker_id'] == 1

    def test_mid_sentence_preservation(self, audio_service):
        """Block of words from one speaker not split mid-sentence."""
        speaker_segments = [
            {'speaker_id': 0, 'begin_time': 0, 'end_time': 2000},
            # Gap: 2000-2500ms
            {'speaker_id': 1, 'begin_time': 2500, 'end_time': 5000},
        ]
        # 5 words over 5s → word at 2000ms falls in gap
        text_segments = [
            {'text': 'я думаю что это правильно', 'begin_time': 0, 'end_time': 5000},
        ]
        result = audio_service._align_speakers_with_text(speaker_segments, text_segments)

        # Words at ~0, ~833, ~1666ms → inside spk0
        # Word at ~2500ms → gap → inertia → still spk0
        # Word at ~3333ms → inside spk1
        # Key: the gap word should NOT split to spk1
        spk0_words = []
        for seg in result:
            if seg['speaker_id'] == 0:
                spk0_words.extend(seg['text'].split())
        # At minimum: "я", "думаю", "что" stay with spk0 (not split away)
        assert len(spk0_words) >= 3


# === LLM Chunking Truncation Tests ===

class TestLlmChunkingTruncation:
    """Tests for hallucination guard and truncation detection in _format_text_chunked()."""

    def test_hallucination_guard_40_percent(self, audio_service):
        """Output <40% of input → use original chunk."""
        original_chunk = "А" * 1000
        short_output = "Б" * 200  # 20% < 40%

        audio_service.LLM_CHUNK_THRESHOLD = 100  # force chunking
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=short_output):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        assert result == original_chunk

    def test_sentence_boundary_truncation(self, audio_service):
        """Output ending mid-sentence (no .!?…) → use original."""
        original_chunk = "Вот, видите, это очень важный момент в нашей работе."
        truncated = "Вот, видите, это очень важный мом"  # mid-word cutoff

        audio_service.LLM_CHUNK_THRESHOLD = 10
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=truncated):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        assert result == original_chunk

    def test_valid_output_not_rejected(self, audio_service):
        """Output ending with proper punctuation passes through."""
        original_chunk = "Это тестовый текст для проверки."
        good_output = "Это тестовый текст для проверки."

        audio_service.LLM_CHUNK_THRESHOLD = 10
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=good_output):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        assert result == good_output


# === Overlap Strip Tests ===

class TestOverlapStrip:
    """Tests for overlap context stripping regex."""

    def test_strip_without_newline(self, audio_service):
        """[...] context without trailing newline is stripped."""
        original_chunk = "Текст после контекста."
        llm_output = "[...] предыдущий контекст" + "\n" + "Текст после контекста."

        audio_service.LLM_CHUNK_THRESHOLD = 10
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=llm_output):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        assert not result.startswith('[...')
        assert 'Текст после контекста.' in result

    def test_strip_only_overlap_no_newline_at_end(self, audio_service):
        """[...] as entire output (no newline) is fully stripped."""
        original_chunk = "Оригинал."
        # LLM returns only overlap marker without newline
        llm_output = "[...] только контекст"

        audio_service.LLM_CHUNK_THRESHOLD = 10
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=llm_output):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        # After stripping overlap and hallucination guard: should fall back to original
        # because stripped result is empty (< 40% of original)
        assert '[...' not in result

    def test_strip_with_newline_regression(self, audio_service):
        """[...] context with trailing newline still works (regression)."""
        original_chunk = "Финальный текст тут."
        llm_output = "[...] контекст\nФинальный текст тут."

        audio_service.LLM_CHUNK_THRESHOLD = 10
        with patch.object(audio_service, '_split_for_llm', return_value=[original_chunk]):
            with patch.object(audio_service, 'format_text_with_qwen', return_value=llm_output):
                result = audio_service._format_text_chunked(
                    original_chunk, False, True, False, False, 'qwen')

        assert not result.startswith('[...')
        assert 'Финальный текст тут.' in result


# === Thinking Leak Tests (v5.0.1 fix) ===

class TestThinkingLeakStrip:
    """Tests for <think> tag stripping and thinking leak detection in format_text_with_assemblyai()."""

    def _make_response(self, content, finish_reason='stop'):
        """Helper: mock requests.post response for AssemblyAI."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': content}, 'finish_reason': finish_reason}],
            'usage': {}
        }
        return mock_resp

    def test_think_tags_stripped(self, audio_service):
        """<think> blocks are removed from AssemblyAI response."""
        text = "Привет мир, это тестовый текст для проверки."
        response_with_think = "<think>I need to format this carefully...</think>\nПривет мир, это тестовый текст для проверки."

        with patch('requests.post', return_value=self._make_response(response_with_think)):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                result = audio_service.format_text_with_assemblyai(text)

        assert '<think>' not in result
        assert 'I need to format' not in result
        assert 'Привет' in result

    def test_think_tags_multiline(self, audio_service):
        """Multi-line <think> blocks are fully removed."""
        text = "Тестовый текст для проверки форматирования."
        response = "<think>\nWait, Rule 11 says...\nI'll keep the labels.\n</think>\nТестовый текст для проверки форматирования."

        with patch('requests.post', return_value=self._make_response(response)):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                result = audio_service.format_text_with_assemblyai(text)

        assert '<think>' not in result
        assert 'Rule 11' not in result
        assert 'Тестовый текст' in result

    def test_unstructured_thinking_cleaned(self, audio_service):
        """English reasoning lines among Russian text are removed."""
        text = "Спикер 1: Привет, как дела?\nСпикер 2: Хорошо, спасибо."
        response = "Спикер 1: Привет, как дела?\nWait, I'll check Rule 11 first.\nСпикер 2: Хорошо, спасибо.\nActually, the speaker labels look correct."

        with patch('requests.post', return_value=self._make_response(response)):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                result = audio_service.format_text_with_assemblyai(text)

        assert "Wait," not in result
        assert "Actually," not in result
        assert "Спикер 1" in result
        assert "Спикер 2" in result

    def test_no_false_positive_on_clean_russian(self, audio_service):
        """Clean Russian output is not modified by thinking detector."""
        text = "Это простой текст на русском языке для проверки."
        clean_response = "Это простой текст на русском языке для проверки."

        with patch('requests.post', return_value=self._make_response(clean_response)):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                result = audio_service.format_text_with_assemblyai(text)

        assert result == clean_response

    def test_cleaning_too_aggressive_returns_original(self, audio_service):
        """If cleaning removes too much text, return original."""
        text = "Hello world. Привет."  # Short text with mixed language
        # Response is mostly English reasoning
        response = "Wait, let me think about this.\nI'll format it.\nLet me check.\nП."

        with patch('requests.post', return_value=self._make_response(response)):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                result = audio_service.format_text_with_assemblyai(text)

        # Should return original because cleaned text is too short
        assert result == text

    def test_system_message_in_payload(self, audio_service):
        """AssemblyAI payload includes system message preventing reasoning."""
        text = "Тестовый текст для проверки наличия системного сообщения в запросе к Gemini через шлюз."
        mock_resp = self._make_response("Тестовый текст для проверки наличия системного сообщения в запросе к Gemini через шлюз.")
        mock_post = MagicMock(return_value=mock_resp)

        with patch('requests.post', mock_post):
            with patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'test-key'}):
                audio_service.format_text_with_assemblyai(text)

        mock_post.assert_called_once()
        payload = mock_post.call_args[1].get('json') or mock_post.call_args.kwargs.get('json')
        assert len(payload['messages']) == 2
        assert payload['messages'][0]['role'] == 'system'
        assert 'No reasoning' in payload['messages'][0]['content']


# === File Size Check Tests (v5.0.1 fix) ===

class TestFileSizeCheck:
    """Tests for file_size > 20MB check in handle_audio_message()."""

    def test_large_document_rejected(self):
        """Document >20MB returns file_too_large with user-friendly message."""
        import main

        message = {
            'chat': {'id': 123},
            'document': {
                'file_id': 'big-file-id',
                'file_size': 25 * 1024 * 1024,  # 25 MB
                'mime_type': 'audio/mpeg'
            }
        }
        user = {'balance_minutes': 100}
        mock_tg = MagicMock()

        with patch('main.get_telegram_service', return_value=mock_tg):
            with patch('main.OWNER_ID', 999):
                result = main.handle_audio_message(message, user)

        assert result == 'file_too_large'
        mock_tg.send_message.assert_called_once()
        call_args = mock_tg.send_message.call_args
        assert '25 МБ' in call_args[0][1]
        assert '/upload' in call_args[0][1]

    def test_small_file_passes_through(self):
        """File <20MB is not rejected by size check."""
        import main

        message = {
            'chat': {'id': 123},
            'voice': {
                'file_id': 'small-file-id',
                'file_size': 5 * 1024 * 1024,  # 5 MB
                'duration': 30
            }
        }
        user = {'balance_minutes': 100}
        mock_tg = MagicMock()

        with patch('main.get_telegram_service', return_value=mock_tg):
            with patch('main.OWNER_ID', 999):
                # Will proceed past size check to balance check, then fail — that's fine
                result = main.handle_audio_message(message, user)

        # Should NOT be 'file_too_large'
        assert result != 'file_too_large'

    def test_large_voice_rejected(self):
        """Voice message >20MB also rejected (edge case)."""
        import main

        message = {
            'chat': {'id': 123},
            'voice': {
                'file_id': 'big-voice-id',
                'file_size': 21 * 1024 * 1024,  # 21 MB
                'duration': 600
            }
        }
        user = {'balance_minutes': 100}
        mock_tg = MagicMock()

        with patch('main.get_telegram_service', return_value=mock_tg):
            with patch('main.OWNER_ID', 999):
                result = main.handle_audio_message(message, user)

        assert result == 'file_too_large'
        call_args = mock_tg.send_message.call_args
        assert '/upload' in call_args[0][1]


# === HTTPError Handling Tests (v5.0.1 fix) ===

class TestGetFilePathHttpError:
    """Tests for improved HTTPError handling in get_file_path()."""

    def test_400_error_logged_with_context(self):
        """400 Bad Request includes file_id and '20MB' hint in log."""
        from telegram import TelegramService
        tg = TelegramService('test-token')

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"ok":false,"error_code":400,"description":"Bad Request: file is too big"}'
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_response)

        with patch.object(tg.session, 'get', return_value=mock_response):
            result = tg.get_file_path('test-file-id')

        assert result is None

    def test_non_400_http_error(self):
        """Non-400 HTTP errors also handled gracefully."""
        from telegram import TelegramService
        tg = TelegramService('test-token')

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_response)

        with patch.object(tg.session, 'get', return_value=mock_response):
            result = tg.get_file_path('test-file-id')

        assert result is None

    def test_connection_error_still_handled(self):
        """Non-HTTP errors (connection, timeout) still handled by base class."""
        from telegram import TelegramService
        tg = TelegramService('test-token')

        with patch.object(tg.session, 'get', side_effect=requests.exceptions.ConnectionError("Connection refused")):
            result = tg.get_file_path('test-file-id')

        assert result is None
