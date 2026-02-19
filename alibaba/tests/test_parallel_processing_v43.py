#!/usr/bin/env python3
"""
Unit tests for parallel processing (v4.3.0).
- SYNC_PROCESSING_THRESHOLD lowered to 15s
- DIARIZATION_THRESHOLD = 60s in audio-processor
- Document duration=0 detection and balance re-check
Run with: python -m pytest alibaba/tests/test_parallel_processing_v43.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

import pytest


# === Webhook routing tests ===

class TestSyncProcessingThreshold:
    """Test that SYNC_PROCESSING_THRESHOLD is 15s."""

    def test_threshold_value_is_15(self):
        """SYNC_PROCESSING_THRESHOLD must be 15."""
        import main as webhook_main
        assert webhook_main.SYNC_PROCESSING_THRESHOLD == 15

    def test_short_audio_below_15s_sync(self):
        """Audio 10s < 15s threshold -> sync path."""
        import main as webhook_main
        duration = 10
        assert duration < webhook_main.SYNC_PROCESSING_THRESHOLD

    def test_audio_15s_async(self):
        """Audio 15s >= 15s threshold -> async path."""
        import main as webhook_main
        duration = 15
        assert duration >= webhook_main.SYNC_PROCESSING_THRESHOLD

    def test_audio_14s_sync(self):
        """Audio 14s < 15s threshold -> sync path."""
        import main as webhook_main
        duration = 14
        assert duration < webhook_main.SYNC_PROCESSING_THRESHOLD


# === Audio-processor diarization threshold tests ===

# Patch env vars before importing handler
@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('OWNER_ID', '999')
    monkeypatch.setenv('TABLESTORE_ENDPOINT', 'https://test.ots.aliyuncs.com')
    monkeypatch.setenv('TABLESTORE_INSTANCE', 'test')
    monkeypatch.setenv('ALIBABA_ACCESS_KEY', 'test-ak')
    monkeypatch.setenv('ALIBABA_SECRET_KEY', 'test-sk')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_job.return_value = None
    db.update_job.return_value = True
    db.get_user.return_value = {
        'balance_minutes': 100,
        'settings': '{}',
    }
    db.update_user_balance.return_value = True
    db.log_transcription.return_value = True
    return db


@pytest.fixture
def mock_tg():
    tg = MagicMock()
    tg.send_message.return_value = {'ok': True, 'result': {'message_id': 42}}
    tg.edit_message_text.return_value = {'ok': True}
    tg.send_chat_action.return_value = True
    tg.get_file_path.return_value = 'file/path.ogg'
    tg.download_file.return_value = '/tmp/test_audio.ogg'
    tg.delete_message.return_value = True
    return tg


@pytest.fixture
def mock_audio():
    audio = MagicMock()
    audio.prepare_audio_for_asr.return_value = '/tmp/test_audio.mp3'
    audio.transcribe_audio.return_value = 'Transcribed text from simple ASR.'
    audio.transcribe_with_diarization.return_value = ('Text with diarization.', [
        {'speaker_id': 1, 'text': 'Hello', 'start': 0, 'end': 5},
        {'speaker_id': 2, 'text': 'Hi there', 'start': 5, 'end': 10},
    ])
    audio.format_dialogue.return_value = '— Hello\n— Hi there'
    audio.get_audio_duration.return_value = 30.0
    audio.get_diarization_debug.return_value = None
    audio.format_text_with_llm.return_value = 'Formatted text.'
    audio.ASR_MAX_CHUNK_DURATION = 600
    return audio


def _make_job_data(duration=30, job_id='test-job-1', status_message_id=42):
    return {
        'job_id': job_id,
        'user_id': '12345',
        'chat_id': '67890',
        'file_id': 'file-abc',
        'file_type': 'voice',
        'duration': duration,
        'status_message_id': status_message_id,
    }


class TestAudioProcessorDiarization:
    """Test conditional diarization in audio-processor."""

    def test_short_audio_skips_diarization(self, mock_db, mock_tg, mock_audio):
        """Audio 30s < 60s threshold -> transcribe_audio (no diarization)."""
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_audio.transcribe_audio.assert_called_once()
        mock_audio.transcribe_with_diarization.assert_not_called()

    def test_long_audio_runs_diarization(self, mock_db, mock_tg, mock_audio):
        """Audio 120s >= 60s threshold -> transcribe_with_diarization."""
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=120))

        assert result['ok'] is True
        mock_audio.transcribe_with_diarization.assert_called_once()
        mock_audio.transcribe_audio.assert_not_called()

    def test_diarization_boundary_60s(self, mock_db, mock_tg, mock_audio):
        """Audio exactly 60s >= 60s threshold -> diarization."""
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=60))

        assert result['ok'] is True
        mock_audio.transcribe_with_diarization.assert_called_once()

    def test_diarization_boundary_59s(self, mock_db, mock_tg, mock_audio):
        """Audio 59s < 60s threshold -> simple ASR."""
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=59))

        assert result['ok'] is True
        mock_audio.transcribe_audio.assert_called_once()
        mock_audio.transcribe_with_diarization.assert_not_called()


class TestDocumentDurationDetection:
    """Test duration=0 document handling."""

    def test_duration_zero_calls_get_audio_duration(self, mock_db, mock_tg, mock_audio):
        """Document with duration=0 should detect real duration via get_audio_duration."""
        mock_audio.get_audio_duration.return_value = 25.0
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        # Called for duration detection + later for is_chunked check
        mock_audio.get_audio_duration.assert_any_call('/tmp/test_audio.mp3')
        assert mock_audio.get_audio_duration.call_count >= 1
        # 25s < 60s -> simple ASR
        mock_audio.transcribe_audio.assert_called_once()
        mock_audio.transcribe_with_diarization.assert_not_called()

    def test_duration_zero_long_audio_diarization(self, mock_db, mock_tg, mock_audio):
        """Document with duration=0 but real 90s -> diarization."""
        mock_audio.get_audio_duration.return_value = 90.0
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        mock_audio.transcribe_with_diarization.assert_called_once()

    def test_document_insufficient_balance_rejection(self, mock_db, mock_tg, mock_audio):
        """Document duration=0, real=1800s (30 min), balance=5 min -> rejection."""
        mock_audio.get_audio_duration.return_value = 1800.0
        mock_db.get_user.return_value = {
            'balance_minutes': 5,
            'settings': '{}',
        }
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        assert result['result'] == 'insufficient_balance'
        # Should send rejection message with deficit info
        sent_msg = mock_tg.send_message.call_args_list
        balance_msg_found = any(
            'Не хватает:' in str(c) or 'buy_minutes' in str(c)
            for c in sent_msg
        )
        assert balance_msg_found, f"Expected balance rejection message, got: {sent_msg}"
        # Should NOT attempt transcription
        mock_audio.transcribe_audio.assert_not_called()
        mock_audio.transcribe_with_diarization.assert_not_called()

    def test_document_sufficient_balance_proceeds(self, mock_db, mock_tg, mock_audio):
        """Document duration=0, real=120s (2 min), balance=100 -> proceed."""
        mock_audio.get_audio_duration.return_value = 120.0
        import handler
        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        assert result['result'] == 'completed'
        # 120s >= 60s -> diarization
        mock_audio.transcribe_with_diarization.assert_called_once()


class TestDiarizationThresholdConstant:
    """Test DIARIZATION_THRESHOLD constant."""

    def test_diarization_threshold_is_60(self):
        """DIARIZATION_THRESHOLD must be 60."""
        import handler
        assert handler.DIARIZATION_THRESHOLD == 60
