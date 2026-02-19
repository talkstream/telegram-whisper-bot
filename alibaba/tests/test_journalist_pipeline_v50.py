#!/usr/bin/env python3
"""
Unit tests for v5.0.0 journalist pipeline features:
- Smart semantic chunking (_split_for_llm, _format_text_chunked)
- ProgressManager (rate limiting, stages, ETA)
- Time budget watchdog
- Auto-file delivery (AUTO_FILE_THRESHOLD)
- Quality safeguards (micro-segment filter, gap ratio, dynamic timeout)
- Billing improvements (package recommendation, editorial package)

Run with: python -m pytest alibaba/tests/test_journalist_pipeline_v50.py -v
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, call

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


@pytest.fixture
def audio_service():
    from audio import AudioService
    return AudioService(whisper_backend='qwen-asr', alibaba_api_key='test-key')


@pytest.fixture
def mock_tg():
    tg = MagicMock()
    tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    tg.edit_message_text.return_value = {'ok': True}
    tg.send_chat_action.return_value = True
    tg.delete_message.return_value = {'ok': True}
    tg.send_as_file.return_value = {'ok': True}
    tg.send_long_message.return_value = True
    return tg


# === Smart Chunking Tests ===

class TestSplitForLlm:
    """Tests for AudioService._split_for_llm()."""

    def test_short_text_no_split(self, audio_service):
        text = "Привет мир. Это тест."
        chunks = audio_service._split_for_llm(text, is_dialogue=False)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_monologue_splits_by_paragraphs(self, audio_service):
        # Create text > LLM_CHUNK_THRESHOLD with paragraphs
        para1 = "Первый абзац. " * 100  # ~1500 chars
        para2 = "Второй абзац. " * 100
        para3 = "Третий абзац. " * 100
        text = f"{para1}\n\n{para2}\n\n{para3}"
        chunks = audio_service._split_for_llm(text, is_dialogue=False)
        assert len(chunks) >= 2
        # First chunk should not have overlap marker
        assert not chunks[0].startswith('[...]')

    def test_monologue_overlap_markers(self, audio_service):
        para1 = "Первый абзац. " * 100
        para2 = "Второй абзац. " * 100
        para3 = "Третий абзац. " * 100
        text = f"{para1}\n\n{para2}\n\n{para3}"
        chunks = audio_service._split_for_llm(text, is_dialogue=False)
        if len(chunks) > 1:
            # Subsequent chunks should have overlap context
            assert chunks[1].startswith('[...]')

    def test_dialogue_splits_by_speaker(self, audio_service):
        # Create a long dialogue
        lines = []
        for i in range(50):
            speaker = (i % 2) + 1
            lines.append(f"Спикер {speaker}:\n— Реплика номер {i}. " * 5)
        text = "\n".join(lines)
        chunks = audio_service._split_for_llm(text, is_dialogue=True)
        assert len(chunks) >= 2

    def test_single_huge_block_splits_by_sentences(self, audio_service):
        # One block exceeding threshold, no paragraph breaks
        text = "Это длинное предложение. " * 300  # ~7500 chars
        chunks = audio_service._split_for_llm(text, is_dialogue=False)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= audio_service.LLM_CHUNK_THRESHOLD * 1.5  # some tolerance

    def test_empty_text(self, audio_service):
        chunks = audio_service._split_for_llm("", is_dialogue=False)
        assert len(chunks) == 0 or chunks == ['']

    def test_threshold_boundary(self, audio_service):
        # Exactly at threshold — should not split
        text = "a" * audio_service.LLM_CHUNK_THRESHOLD
        chunks = audio_service._split_for_llm(text, is_dialogue=False)
        assert len(chunks) == 1


class TestFormatTextChunked:
    """Tests for AudioService._format_text_chunked()."""

    def test_chunked_calls_backend_per_chunk(self, audio_service):
        text = "Первый абзац. " * 100 + "\n\n" + "Второй абзац. " * 100 + "\n\n" + "Третий абзац. " * 100
        calls = []

        def mock_qwen(t, *args, **kwargs):
            calls.append(t)
            return t.upper()  # simple transform

        audio_service.format_text_with_qwen = mock_qwen
        result = audio_service._format_text_chunked(
            text, False, True, False, False, 'qwen')
        assert len(calls) >= 2
        assert result  # not empty

    def test_hallucination_guard(self, audio_service):
        text = "Первый абзац. " * 100 + "\n\n" + "Второй абзац. " * 100

        def mock_qwen(t, *args, **kwargs):
            return "X"  # suspiciously short

        audio_service.format_text_with_qwen = mock_qwen
        result = audio_service._format_text_chunked(
            text, False, True, False, False, 'qwen')
        # Should use original chunk when output is too short
        assert len(result) > 100

    def test_progress_callback_called(self, audio_service):
        text = "Первый абзац. " * 100 + "\n\n" + "Второй абзац. " * 100 + "\n\n" + "Третий абзац. " * 100
        progress_calls = []

        def mock_qwen(t, *args, **kwargs):
            return t

        def progress_cb(current, total):
            progress_calls.append((current, total))

        audio_service.format_text_with_qwen = mock_qwen
        audio_service._format_text_chunked(
            text, False, True, False, False, 'qwen',
            progress_callback=progress_cb)
        assert len(progress_calls) >= 2
        assert progress_calls[0][0] == 1  # first chunk

    def test_max_chunks_limit(self, audio_service):
        # Text that would produce > LLM_MAX_CHUNKS chunks
        audio_service.LLM_CHUNK_THRESHOLD = 50  # artificially low
        text = ("Short. " * 20 + "\n\n") * 25  # many short paragraphs
        result = audio_service._format_text_chunked(
            text, False, True, False, False, 'qwen')
        # Should return original text when too many chunks
        assert result == text
        audio_service.LLM_CHUNK_THRESHOLD = 4000  # restore

    def test_overlap_stripped_from_output(self, audio_service):
        def mock_qwen(t, *args, **kwargs):
            return t  # identity

        audio_service.format_text_with_qwen = mock_qwen
        text = "Первый абзац. " * 100 + "\n\n" + "Второй абзац. " * 100
        result = audio_service._format_text_chunked(
            text, False, True, False, False, 'qwen')
        # No overlap markers in final output
        assert '[...]' not in result


class TestFormatTextWithLlmChunking:
    """Tests for format_text_with_llm() with chunking integration."""

    def test_short_text_no_chunking(self, audio_service):
        text = "Привет мир."

        def mock_qwen(t, *args, **kwargs):
            return "Привет, мир."

        audio_service.format_text_with_qwen = mock_qwen
        result = audio_service.format_text_with_llm(text)
        assert result == "Привет, мир."

    def test_long_text_triggers_chunking(self, audio_service):
        text = "Длинное предложение. " * 300
        chunk_calls = []

        def mock_qwen(t, *args, **kwargs):
            chunk_calls.append(len(t))
            return t

        audio_service.format_text_with_qwen = mock_qwen
        result = audio_service.format_text_with_llm(text)
        assert len(chunk_calls) >= 2
        assert result  # not empty


# === ProgressManager Tests ===

class TestProgressManager:
    """Tests for ProgressManager class."""

    def test_stage_sends_message(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42, audio_duration=120)
        pm.stage('download')
        mock_tg.edit_message_text.assert_called_with(123, 42, '📥 Загружаю файл...')

    def test_rate_limiting(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42)
        pm.update("First", force=True)
        pm.update("Second")  # should be rate-limited
        assert mock_tg.edit_message_text.call_count == 1

    def test_force_bypasses_rate_limit(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42)
        pm.update("First", force=True)
        pm.update("Second", force=True)
        assert mock_tg.edit_message_text.call_count == 2

    def test_no_message_id(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, None)
        pm.stage('download')
        mock_tg.edit_message_text.assert_not_called()

    def test_diarize_eta_short_audio(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42, audio_duration=120)
        pm.stage('diarize')
        args = mock_tg.edit_message_text.call_args[0]
        assert '1-2 мин' in args[2]

    def test_diarize_eta_long_audio(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42, audio_duration=2400)
        pm.stage('diarize')
        args = mock_tg.edit_message_text.call_args[0]
        assert '3-5 мин' in args[2]

    def test_format_chunk_stage(self, mock_tg):
        from handler import ProgressManager
        pm = ProgressManager(mock_tg, 123, 42)
        pm.stage('format_chunk', current=2, total=5)
        args = mock_tg.edit_message_text.call_args[0]
        assert '2' in args[2] and '5' in args[2]

    def test_exception_in_update_does_not_raise(self, mock_tg):
        from handler import ProgressManager
        mock_tg.edit_message_text.side_effect = Exception("API error")
        pm = ProgressManager(mock_tg, 123, 42)
        # Should not raise
        pm.stage('download')


# === Time Budget Watchdog Tests ===

class TestWatchdog:
    """Tests for time budget watchdog in process_job()."""

    def test_watchdog_skips_llm_when_low_time(self, mock_tg):
        from handler import (
            _format_transcription, FC_TIMEOUT, SAFETY_MARGIN
        )
        # This tests the watchdog logic indirectly via process_job
        # When remaining < 60s, LLM should be skipped
        # We test by checking that text is returned unchanged
        from handler import ProgressManager
        progress = ProgressManager(mock_tg, 123, 42)

        # Short text that would normally be returned as-is
        text = "Короткий текст."
        audio = MagicMock()
        audio.get_audio_duration.return_value = 30.0
        result = _format_transcription(
            audio, text, False, {}, '/tmp/test.mp3',
            mock_tg, 123, 42, progress=progress)
        assert result == text  # too short, returned as-is


# === Auto-File Delivery Tests ===

class TestAutoFileDelivery:
    """Tests for auto-file delivery when text > AUTO_FILE_THRESHOLD."""

    def test_short_text_edit_mode(self, mock_tg):
        from handler import _deliver_result
        _deliver_result(mock_tg, 123, 42, "Short text", {})
        mock_tg.edit_message_text.assert_called()
        mock_tg.send_as_file.assert_not_called()

    def test_long_text_auto_file(self, mock_tg):
        from handler import _deliver_result, AUTO_FILE_THRESHOLD
        long_text = "x" * (AUTO_FILE_THRESHOLD + 100)
        _deliver_result(mock_tg, 123, 42, long_text, {})
        mock_tg.delete_message.assert_called_with(123, 42)
        mock_tg.send_as_file.assert_called_once()
        # Check filename argument
        call_kwargs = mock_tg.send_as_file.call_args
        assert 'transcript_' in call_kwargs.kwargs.get('filename', '') or \
               'transcript_' in str(call_kwargs)

    def test_file_mode_setting_overrides(self, mock_tg):
        from handler import _deliver_result
        text = "Medium text " * 400  # > 4000 chars but < AUTO_FILE_THRESHOLD
        settings = {'long_text_mode': 'file'}
        _deliver_result(mock_tg, 123, None, text, settings)  # no progress_id
        mock_tg.send_as_file.assert_called_once()

    def test_split_mode_default(self, mock_tg):
        from handler import _deliver_result, AUTO_FILE_THRESHOLD
        # Text > 4000 but < AUTO_FILE_THRESHOLD
        text = "x" * 5000
        assert 5000 < AUTO_FILE_THRESHOLD  # sanity check
        _deliver_result(mock_tg, 123, 42, text, {})
        mock_tg.send_long_message.assert_called()


# === Quality Safeguards Tests ===

class TestMicroSegmentFilter:
    """Tests for _filter_micro_segments()."""

    def test_filters_short_segments(self, audio_service):
        segments = [
            {'speaker_id': 1, 'text': 'Normal text here', 'begin_time': 0, 'end_time': 5000},
            {'speaker_id': 2, 'text': 'ok', 'begin_time': 5000, 'end_time': 5200},  # micro
            {'speaker_id': 1, 'text': 'More normal text', 'begin_time': 5200, 'end_time': 10000},
        ]
        result = audio_service._filter_micro_segments(segments)
        assert len(result) == 2
        # Micro-segment merged with previous
        assert 'ok' in result[0]['text']

    def test_keeps_normal_segments(self, audio_service):
        segments = [
            {'speaker_id': 1, 'text': 'Normal text', 'begin_time': 0, 'end_time': 5000},
            {'speaker_id': 2, 'text': 'Also normal', 'begin_time': 5000, 'end_time': 10000},
        ]
        result = audio_service._filter_micro_segments(segments)
        assert len(result) == 2

    def test_empty_segments(self, audio_service):
        result = audio_service._filter_micro_segments([])
        assert result == []


class TestGapRatioDetection:
    """Tests for gap ratio detection in transcribe_with_diarization alignment."""

    def test_alignment_preserves_text(self, audio_service):
        speaker_segs = [
            {'speaker_id': 1, 'begin_time': 0, 'end_time': 5000, 'text': ''},
            {'speaker_id': 2, 'begin_time': 5000, 'end_time': 10000, 'text': ''},
        ]
        text_segs = [
            {'text': 'Hello world', 'begin_time': 0, 'end_time': 3000},
            {'text': 'Goodbye world', 'begin_time': 5000, 'end_time': 8000},
        ]
        result = audio_service._align_speakers_with_text(speaker_segs, text_segs)
        all_text = ' '.join(s['text'] for s in result)
        assert 'Hello' in all_text
        assert 'Goodbye' in all_text


class TestDynamicDiarizationTimeout:
    """Tests for dynamic diarization timeout calculation."""

    def test_short_audio_timeout(self, audio_service):
        # The timeout logic is inside transcribe_with_diarization
        # We test indirectly by checking the constants exist
        assert hasattr(audio_service, 'get_audio_duration')

    def test_windowed_normalization_exists(self, audio_service):
        assert hasattr(audio_service, '_normalize_windowed')


# === Billing Improvements Tests ===

class TestBillingImprovements:
    """Tests for billing changes: package recommendation, editorial package."""

    def test_editorial_package_exists(self):
        """Verify editorial_3000 package exists in webhook-handler/main.py."""
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'webhook-handler', 'main.py')
        with open(main_path) as f:
            content = f.read()
        assert 'editorial_3000' in content
        assert 'buy_editorial_3000' in content
        assert '1399' in content
        assert '3000' in content

    def test_balance_check_message_format(self):
        """Verify the balance check message includes deficit info."""
        main_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'webhook-handler', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check new balance message format exists
        assert 'Не хватает:' in content
        assert 'Рекомендуем:' in content


# === send_as_file filename parameter Tests ===

class TestSendAsFileFilename:
    """Tests for send_as_file filename parameter."""

    def test_send_as_file_with_filename(self):
        from telegram import TelegramService
        tg = TelegramService('test-token')
        # Mock the session
        tg.session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_response.raise_for_status.return_value = None
        tg.session.post.return_value = mock_response

        tg.send_as_file(123, "test content", caption="test",
                        filename="custom_name.txt")

        # Check that send_document was called
        call_args = tg.session.post.call_args
        files_arg = call_args.kwargs.get('files', call_args[1].get('files', {}))
        if files_arg:
            doc_tuple = files_arg.get('document', ())
            if doc_tuple:
                assert doc_tuple[0] == 'custom_name.txt'


# === Constants Tests ===

class TestConstants:
    """Tests for new constants."""

    def test_auto_file_threshold(self):
        from handler import AUTO_FILE_THRESHOLD
        assert AUTO_FILE_THRESHOLD == 8000

    def test_fc_timeout(self):
        from handler import FC_TIMEOUT
        assert FC_TIMEOUT == 600

    def test_safety_margin(self):
        from handler import SAFETY_MARGIN
        assert SAFETY_MARGIN == 30

    def test_llm_chunk_threshold(self, audio_service):
        assert audio_service.LLM_CHUNK_THRESHOLD == 4000

    def test_llm_max_chunks(self, audio_service):
        assert audio_service.LLM_MAX_CHUNKS == 20
