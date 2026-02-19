#!/usr/bin/env python3
"""
Unit tests for audio-processor/handler.py — handler(), process_mns_message(), process_job().
Covers: MNS parsing, success/error flows, balance deduction, temp cleanup,
delivery modes (edit/file/split), dedup, duration=0 detection, diarization threshold,
low balance warnings, error message routing.
Run with: python -m pytest alibaba/tests/test_audio_processor.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))

import pytest


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
    tg.send_long_message.return_value = True
    tg.send_as_file.return_value = True
    return tg


@pytest.fixture
def mock_audio():
    audio = MagicMock()
    audio.prepare_audio_for_asr.return_value = '/tmp/test_audio.mp3'
    audio.transcribe_audio.return_value = 'Transcribed text from simple ASR path.'
    audio.transcribe_with_diarization.return_value = ('Diarized raw text.', [
        {'speaker_id': 1, 'text': 'Hello', 'start': 0, 'end': 5},
        {'speaker_id': 2, 'text': 'Hi there', 'start': 5, 'end': 10},
    ])
    audio.format_dialogue.return_value = '— Hello\n— Hi there'
    audio.get_audio_duration.return_value = 30.0
    audio.get_diarization_debug.return_value = None
    audio.format_text_with_llm.return_value = 'Formatted text from LLM.'
    audio.ASR_MAX_CHUNK_DURATION = 600
    return audio


@pytest.fixture
def patch_services(mock_db, mock_tg, mock_audio):
    """Context manager fixture that patches all three service getters."""
    import handler
    with patch.object(handler, 'get_db_service', return_value=mock_db), \
         patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
         patch.object(handler, 'get_audio_service', return_value=mock_audio), \
         patch('os.remove'):
        yield


def _make_job_data(duration=30, job_id='job-001', status_message_id=42):
    return {
        'job_id': job_id,
        'user_id': '12345',
        'chat_id': '67890',
        'file_id': 'file-abc',
        'file_type': 'voice',
        'duration': duration,
        'status_message_id': status_message_id,
    }


# ==================== handler() MNS event parsing ====================

class TestHandlerEventParsing:
    """Test handler() parses different event formats correctly."""

    def test_handler_parses_bytes_event(self, patch_services, mock_audio):
        """handler() decodes bytes event and routes to process_mns_message."""
        import handler
        job_data = _make_job_data()
        event = json.dumps({'Message': json.dumps(job_data)}).encode('utf-8')

        result = handler.handler(event, None)

        assert result['ok'] is True

    def test_handler_parses_string_event(self, patch_services, mock_audio):
        """handler() parses string event and routes to process_mns_message."""
        import handler
        job_data = _make_job_data()
        event = json.dumps({'Message': json.dumps(job_data)})

        result = handler.handler(event, None)

        assert result['ok'] is True

    def test_handler_routes_mns_message(self, patch_services, mock_audio):
        """handler() routes event with 'Message' key to process_mns_message."""
        import handler
        job_data = _make_job_data()
        event = {'Message': json.dumps(job_data)}

        result = handler.handler(event, None)

        assert result['ok'] is True

    def test_handler_routes_direct_job_id(self, patch_services, mock_audio):
        """handler() routes event with 'job_id' to process_mns_message."""
        import handler
        event = _make_job_data()

        result = handler.handler(event, None)

        assert result['ok'] is True

    def test_handler_routes_body_event(self, patch_services, mock_audio):
        """handler() routes event with 'body' to process_job."""
        import handler
        job_data = _make_job_data()
        event = {'body': json.dumps(job_data)}

        result = handler.handler(event, None)

        assert result['ok'] is True

    def test_handler_unknown_format_returns_200(self):
        """handler() returns 200 for unknown event format."""
        import handler
        result = handler.handler({'random_key': 'value'}, None)

        assert result['statusCode'] == 200
        assert 'Unknown event format' in result['body']

    def test_handler_invalid_json_returns_500(self):
        """handler() returns 500 for malformed JSON bytes."""
        import handler
        result = handler.handler(b'not-json', None)

        assert result['statusCode'] == 500

    def test_handler_timer_trigger_with_poll_action(self, patch_services):
        """handler() routes timer trigger with poll_queue action."""
        import handler
        event = {
            'triggerName': 'audio-timer',
            'triggerTime': '2026-02-19T10:00:00Z',
            'payload': json.dumps({'action': 'poll_queue'}),
        }

        with patch.object(handler, 'poll_queue', return_value={'statusCode': 200, 'body': 'No messages in queue'}) as mock_poll:
            result = handler.handler(event, None)

        mock_poll.assert_called_once()
        assert result['statusCode'] == 200


# ==================== process_mns_message() ====================

class TestProcessMnsMessage:
    """Test MNS message extraction."""

    def test_mns_message_with_string_body(self, patch_services, mock_audio):
        """process_mns_message extracts job_data from JSON string Message."""
        import handler
        job_data = _make_job_data()
        event = {'Message': json.dumps(job_data)}

        result = handler.process_mns_message(event)

        assert result['ok'] is True

    def test_mns_message_with_dict_body(self, patch_services, mock_audio):
        """process_mns_message extracts job_data from dict Message."""
        import handler
        job_data = _make_job_data()
        event = {'Message': job_data}

        result = handler.process_mns_message(event)

        assert result['ok'] is True

    def test_mns_message_fallback_to_event(self, patch_services, mock_audio):
        """process_mns_message uses event directly when no 'Message' key."""
        import handler
        event = _make_job_data()

        result = handler.process_mns_message(event)

        assert result['ok'] is True


# ==================== process_job() — validation ====================

class TestProcessJobValidation:
    """Test process_job input validation."""

    def test_missing_job_id_returns_error(self):
        """process_job rejects job_data without job_id."""
        import handler
        data = _make_job_data()
        del data['job_id']

        result = handler.process_job(data)

        assert result['ok'] is False
        assert 'Missing required fields' in result['error']

    def test_missing_user_id_returns_error(self):
        """process_job rejects job_data without user_id."""
        import handler
        data = _make_job_data()
        del data['user_id']

        result = handler.process_job(data)

        assert result['ok'] is False

    def test_missing_chat_id_returns_error(self):
        """process_job rejects job_data without chat_id."""
        import handler
        data = _make_job_data()
        del data['chat_id']

        result = handler.process_job(data)

        assert result['ok'] is False

    def test_missing_file_id_returns_error(self):
        """process_job rejects job_data without file_id."""
        import handler
        data = _make_job_data()
        del data['file_id']

        result = handler.process_job(data)

        assert result['ok'] is False


# ==================== process_job() — deduplication ====================

class TestProcessJobDedup:
    """Test MNS at-least-once deduplication."""

    def test_skip_already_processing_job(self, mock_db, mock_tg, mock_audio):
        """Job with status='processing' is skipped (MNS redelivery)."""
        import handler
        mock_db.get_job.return_value = {'status': 'processing'}

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        assert result['result'] == 'duplicate'
        # Should NOT attempt transcription
        mock_audio.transcribe_audio.assert_not_called()

    def test_skip_already_completed_job(self, mock_db, mock_tg, mock_audio):
        """Job with status='completed' is skipped (MNS redelivery)."""
        import handler
        mock_db.get_job.return_value = {'status': 'completed'}

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        assert result['result'] == 'duplicate'

    def test_pending_job_proceeds(self, patch_services, mock_db, mock_audio):
        """Job with status='pending' is NOT skipped — proceeds normally."""
        import handler
        mock_db.get_job.return_value = {'status': 'pending'}

        result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        assert result['result'] == 'completed'


# ==================== process_job() — success flow ====================

class TestProcessJobSuccess:
    """Test successful transcription flow."""

    def test_short_text_edits_progress_message(self, patch_services, mock_db, mock_tg, mock_audio):
        """Short result (<=4000 chars) edits existing progress message."""
        import handler
        mock_audio.transcribe_audio.return_value = 'Short text.'
        mock_audio.get_audio_duration.return_value = 30.0

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        # Final edit should contain result text (not a progress message)
        final_edit_calls = [
            c for c in mock_tg.edit_message_text.call_args_list
            if 'Short text.' in str(c)
        ]
        assert len(final_edit_calls) >= 1
        # delete_message NOT called for short text
        mock_tg.send_long_message.assert_not_called()

    def test_balance_deducted_on_success(self, patch_services, mock_db, mock_tg, mock_audio):
        """Balance is deducted after transcription, before delivery."""
        import handler
        result = handler.process_job(_make_job_data(duration=90))

        assert result['ok'] is True
        # 90s -> ceil(90/60) = 2 minutes
        mock_db.update_user_balance.assert_called_once_with(12345, -2)

    def test_balance_deduction_rounds_up(self, patch_services, mock_db, mock_tg, mock_audio):
        """Balance deduction rounds up: 61s -> 2 minutes."""
        import handler
        result = handler.process_job(_make_job_data(duration=61))

        assert result['ok'] is True
        mock_db.update_user_balance.assert_called_once_with(12345, -2)

    def test_job_status_set_to_completed(self, patch_services, mock_db, mock_tg, mock_audio):
        """Job status updated to 'completed' after success."""
        import handler
        result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        # Last update_job call should set status='completed'
        final_call = mock_db.update_job.call_args_list[-1]
        assert final_call[0][1]['status'] == 'completed'

    def test_transcription_logged(self, patch_services, mock_db, mock_tg, mock_audio):
        """log_transcription called with correct user_id and duration."""
        import handler
        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_db.log_transcription.assert_called_once()
        log_data = mock_db.log_transcription.call_args[0][0]
        assert log_data['user_id'] == '12345'
        assert log_data['duration'] == 30
        assert log_data['status'] == 'completed'

    def test_progress_message_reused_from_webhook(self, patch_services, mock_db, mock_tg, mock_audio):
        """status_message_id from webhook is reused (edit, not new send)."""
        import handler
        result = handler.process_job(_make_job_data(status_message_id=55))

        assert result['ok'] is True
        # First edit should use the provided status_message_id=55
        first_edit = mock_tg.edit_message_text.call_args_list[0]
        assert first_edit[0][1] == 55

    def test_no_status_message_creates_new(self, patch_services, mock_db, mock_tg, mock_audio):
        """Without status_message_id, a new progress message is sent."""
        import handler
        result = handler.process_job(_make_job_data(status_message_id=None))

        assert result['ok'] is True
        # send_message should be called for initial progress
        assert mock_tg.send_message.call_count >= 1


# ==================== process_job() — delivery modes ====================

class TestDeliveryModes:
    """Test 3-way delivery: edit, file, split."""

    def test_file_mode_sends_as_file(self, mock_db, mock_tg, mock_audio):
        """long_text_mode='file' delivers result as .txt file."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 100,
            'settings': json.dumps({'long_text_mode': 'file'}),
        }
        # Long text to force non-edit delivery
        mock_audio.transcribe_audio.return_value = 'A' * 5000
        mock_audio.format_text_with_llm.return_value = 'B' * 5000

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_tg.send_as_file.assert_called_once()
        mock_tg.delete_message.assert_called()

    def test_split_mode_sends_long_message(self, mock_db, mock_tg, mock_audio):
        """long_text_mode='split' (default) uses send_long_message for long text."""
        import handler
        mock_audio.transcribe_audio.return_value = 'A' * 5000
        mock_audio.format_text_with_llm.return_value = 'B' * 5000

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_tg.send_long_message.assert_called_once()
        mock_tg.delete_message.assert_called()

    def test_short_text_edits_in_place(self, patch_services, mock_db, mock_tg, mock_audio):
        """Short text (<=4000 chars) edits progress message in place."""
        import handler
        mock_audio.transcribe_audio.return_value = 'Short result.'
        mock_audio.get_audio_duration.return_value = 30.0

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        # send_long_message and send_as_file should NOT be called
        mock_tg.send_long_message.assert_not_called()
        mock_tg.send_as_file.assert_not_called()


# ==================== process_job() — empty/no speech ====================

class TestNoSpeechDetection:
    """Test empty transcription handling."""

    def test_empty_text_returns_no_speech(self, patch_services, mock_db, mock_tg, mock_audio):
        """Empty transcription returns no_speech and notifies user."""
        import handler
        mock_audio.transcribe_audio.return_value = ''

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        assert result['result'] == 'no_speech'
        mock_db.update_job.assert_any_call('job-001', {'status': 'failed', 'error': 'no_speech'})

    def test_continuation_text_returns_no_speech(self, patch_services, mock_db, mock_tg, mock_audio):
        """'Продолжение следует...' sentinel returns no_speech."""
        import handler
        mock_audio.transcribe_audio.return_value = 'Продолжение следует...'

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        assert result['result'] == 'no_speech'

    def test_whitespace_only_returns_no_speech(self, patch_services, mock_db, mock_tg, mock_audio):
        """Whitespace-only transcription returns no_speech."""
        import handler
        mock_audio.transcribe_audio.return_value = '   \n  '

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        assert result['result'] == 'no_speech'


# ==================== process_job() — error flows ====================

class TestProcessJobErrors:
    """Test error handling in process_job."""

    def test_file_download_failure(self, mock_db, mock_tg, mock_audio):
        """Failed Telegram file download sends user-friendly error."""
        import handler
        mock_tg.get_file_path.return_value = None

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is False
        mock_db.update_job.assert_any_call('job-001', {
            'status': 'failed', 'error': 'Failed to get file path from Telegram'
        })

    def test_timeout_error_message(self, mock_db, mock_tg, mock_audio):
        """Timeout error sends appropriate user message."""
        import handler
        mock_audio.prepare_audio_for_asr.side_effect = Exception('Request timeout exceeded')

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is False
        # User should get timeout-specific message ("слишком много времени")
        error_msg = mock_tg.send_message.call_args[0][1]
        assert 'времени' in error_msg or 'поменьше' in error_msg

    def test_invalid_parameter_error_message(self, mock_db, mock_tg, mock_audio):
        """InvalidParameter error sends 'too long' user message."""
        import handler
        mock_audio.prepare_audio_for_asr.side_effect = Exception('InvalidParameter: duration too large')

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is False
        error_msg = mock_tg.send_message.call_args[0][1]
        assert 'длинное' in error_msg or 'короче' in error_msg

    def test_generic_error_sends_fallback_message(self, mock_db, mock_tg, mock_audio):
        """Unknown error sends generic user-friendly message."""
        import handler
        mock_audio.prepare_audio_for_asr.side_effect = Exception('Some unexpected error')

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is False
        error_msg = mock_tg.send_message.call_args[0][1]
        assert 'ошибка' in error_msg.lower() or 'позже' in error_msg.lower()

    def test_error_truncated_to_200_chars(self, mock_db, mock_tg, mock_audio):
        """Error message stored in job is truncated to 200 chars."""
        import handler
        long_error = 'x' * 500
        mock_audio.prepare_audio_for_asr.side_effect = Exception(long_error)

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            handler.process_job(_make_job_data())

        # Find the 'failed' update_job call
        failed_calls = [
            c for c in mock_db.update_job.call_args_list
            if c[0][1].get('status') == 'failed'
        ]
        assert len(failed_calls) >= 1
        assert len(failed_calls[-1][0][1]['error']) <= 200


# ==================== process_job() — temp file cleanup ====================

class TestTempFileCleanup:
    """Test finally block cleans up temp files."""

    def test_cleanup_on_success(self, mock_db, mock_tg, mock_audio):
        """Temp files are cleaned up after successful processing."""
        import handler

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove') as mock_remove:
            handler.process_job(_make_job_data())

        # Both local_path and converted_path should be cleaned
        removed_paths = [c[0][0] for c in mock_remove.call_args_list]
        assert '/tmp/test_audio.ogg' in removed_paths
        assert '/tmp/test_audio.mp3' in removed_paths

    def test_cleanup_on_error(self, mock_db, mock_tg, mock_audio):
        """Temp files are cleaned up even when processing fails."""
        import handler
        mock_audio.prepare_audio_for_asr.return_value = '/tmp/test_audio.mp3'
        mock_audio.transcribe_audio.side_effect = Exception('ASR failed')

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove') as mock_remove:
            handler.process_job(_make_job_data())

        # Cleanup should still happen via finally block
        removed_paths = [c[0][0] for c in mock_remove.call_args_list]
        assert '/tmp/test_audio.ogg' in removed_paths
        assert '/tmp/test_audio.mp3' in removed_paths

    def test_cleanup_ignores_os_error(self, mock_db, mock_tg, mock_audio):
        """OSError during cleanup is silently ignored (no crash)."""
        import handler

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove', side_effect=OSError('file not found')):
            result = handler.process_job(_make_job_data())

        # Should complete successfully despite cleanup failure
        assert result['ok'] is True


# ==================== process_job() — low balance warnings ====================

class TestLowBalanceWarnings:
    """Test low balance and exhausted balance notifications."""

    def test_low_balance_warning(self, mock_db, mock_tg, mock_audio):
        """User with 1-4 min remaining gets low balance warning."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 3,
            'settings': '{}',
        }

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=60))

        assert result['ok'] is True
        # Find low balance warning (contains /buy_minutes and 'Низкий баланс')
        warning_calls = [
            c for c in mock_tg.send_message.call_args_list
            if 'Низкий баланс' in str(c)
        ]
        assert len(warning_calls) == 1

    def test_exhausted_balance_warning(self, mock_db, mock_tg, mock_audio):
        """User with 0 min remaining gets 'balance exhausted' warning."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 1,
            'settings': '{}',
        }

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=60))

        assert result['ok'] is True
        # Find exhausted warning (contains 'исчерпан')
        exhausted_calls = [
            c for c in mock_tg.send_message.call_args_list
            if 'исчерпан' in str(c)
        ]
        assert len(exhausted_calls) == 1

    def test_no_warning_when_balance_sufficient(self, patch_services, mock_db, mock_tg, mock_audio):
        """No balance warning when remaining >= 5 minutes."""
        import handler

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        # No warning calls (only result delivery, no 'Низкий баланс' or 'исчерпан')
        warning_calls = [
            c for c in mock_tg.send_message.call_args_list
            if 'Низкий баланс' in str(c) or 'исчерпан' in str(c)
        ]
        assert len(warning_calls) == 0


# ==================== process_job() — balance deduction failure ====================

class TestBalanceDeductionFailure:
    """Test handling when balance deduction fails."""

    def test_balance_failure_notifies_owner(self, mock_db, mock_tg, mock_audio):
        """Failed balance deduction sends alert to OWNER_ID."""
        import handler
        mock_db.update_user_balance.return_value = False

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        # Owner notification about balance error
        owner_calls = [
            c for c in mock_tg.send_message.call_args_list
            if c[0][0] == 999 and 'баланс' in str(c).lower()
        ]
        assert len(owner_calls) == 1

    def test_balance_failure_still_delivers_result(self, mock_db, mock_tg, mock_audio):
        """Result is still delivered even when balance deduction fails."""
        import handler
        mock_db.update_user_balance.return_value = False

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data())

        assert result['ok'] is True
        assert result['result'] == 'completed'


# ==================== process_job() — user settings ====================

class TestUserSettings:
    """Test user settings affect processing."""

    def test_use_code_tags_wraps_in_code(self, mock_db, mock_tg, mock_audio):
        """use_code_tags=True wraps result in <code> tags."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 100,
            'settings': json.dumps({'use_code_tags': True}),
        }
        mock_audio.transcribe_audio.return_value = 'Short text.'
        mock_audio.get_audio_duration.return_value = 30.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        # Final edit should contain <code> tags
        final_edit = mock_tg.edit_message_text.call_args_list[-1]
        assert '<code>' in final_edit[0][2]
        assert final_edit[1].get('parse_mode') == 'HTML'

    def test_use_yo_false_replaces_yo(self, mock_db, mock_tg, mock_audio):
        """use_yo=False replaces all ё with е in short text (no LLM path)."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 100,
            'settings': json.dumps({'use_yo': False}),
        }
        # Short text (<= 100 chars) bypasses LLM
        mock_audio.transcribe_audio.return_value = 'Ёлка ёжик'
        mock_audio.get_audio_duration.return_value = 30.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        final_edit = mock_tg.edit_message_text.call_args_list[-1]
        text_sent = final_edit[0][2]
        assert 'ё' not in text_sent
        assert 'Ё' not in text_sent

    def test_llm_skipped_for_short_text(self, patch_services, mock_db, mock_tg, mock_audio):
        """Text <= 100 chars skips LLM formatting."""
        import handler
        mock_audio.transcribe_audio.return_value = 'Hello world.'
        mock_audio.get_audio_duration.return_value = 30.0

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_audio.format_text_with_llm.assert_not_called()

    def test_llm_called_for_long_text(self, patch_services, mock_db, mock_tg, mock_audio):
        """Text > 100 chars triggers LLM formatting."""
        import handler
        mock_audio.transcribe_audio.return_value = 'A' * 200
        mock_audio.get_audio_duration.return_value = 30.0

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        mock_audio.format_text_with_llm.assert_called_once()


# ==================== process_job() — diarization (speaker detection) ====================

class TestDiarizationSpeakerDetection:
    """Test speaker detection logic in diarization path."""

    def test_two_speakers_formats_dialogue(self, mock_db, mock_tg, mock_audio):
        """2+ speakers -> format_dialogue, is_dialogue=True (LLM skipped)."""
        import handler
        mock_audio.transcribe_with_diarization.return_value = ('Raw text.', [
            {'speaker_id': 1, 'text': 'Hello', 'start': 0, 'end': 5},
            {'speaker_id': 2, 'text': 'Hi', 'start': 5, 'end': 10},
        ])
        mock_audio.format_dialogue.return_value = '— Hello\n— Hi'
        mock_audio.get_audio_duration.return_value = 90.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=90))

        assert result['ok'] is True
        mock_audio.format_dialogue.assert_called_once()
        # LLM should NOT be called for dialogue
        mock_audio.format_text_with_llm.assert_not_called()

    def test_single_speaker_uses_raw_text(self, mock_db, mock_tg, mock_audio):
        """1 speaker -> raw_text (no dashes), goes through LLM."""
        import handler
        raw_text = 'A' * 200  # Long enough to trigger LLM
        mock_audio.transcribe_with_diarization.return_value = (raw_text, [
            {'speaker_id': 1, 'text': raw_text, 'start': 0, 'end': 60},
        ])
        mock_audio.get_audio_duration.return_value = 90.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=90))

        assert result['ok'] is True
        mock_audio.format_dialogue.assert_not_called()
        mock_audio.format_text_with_llm.assert_called_once()

    def test_diarization_failure_falls_back_to_simple_asr(self, mock_db, mock_tg, mock_audio):
        """When diarization returns no segments, fallback to simple ASR."""
        import handler
        mock_audio.transcribe_with_diarization.return_value = (None, None)
        mock_audio.transcribe_audio.return_value = 'Fallback text from simple ASR.'
        mock_audio.get_audio_duration.return_value = 90.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=90))

        assert result['ok'] is True
        mock_audio.transcribe_audio.assert_called_once()


# ==================== process_job() — duration=0 detection ====================

class TestDurationZeroDetection:
    """Test document with duration=0 (forwarded audio files)."""

    def test_duration_zero_detects_real_duration(self, mock_db, mock_tg, mock_audio):
        """duration=0 triggers get_audio_duration for real length detection."""
        import handler
        mock_audio.get_audio_duration.return_value = 25.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        # get_audio_duration called at least once for detection
        mock_audio.get_audio_duration.assert_called()

    def test_duration_zero_insufficient_balance_rejected(self, mock_db, mock_tg, mock_audio):
        """duration=0, real=1800s (30 min), balance=5 -> rejection."""
        import handler
        mock_audio.get_audio_duration.return_value = 1800.0
        mock_db.get_user.return_value = {
            'balance_minutes': 5,
            'settings': '{}',
        }

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        assert result['result'] == 'insufficient_balance'
        mock_audio.transcribe_audio.assert_not_called()

    def test_duration_zero_uses_detected_for_balance(self, mock_db, mock_tg, mock_audio):
        """duration=0, real=90s -> balance deducted for 2 minutes (ceil(90/60))."""
        import handler
        mock_audio.get_audio_duration.return_value = 90.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            result = handler.process_job(_make_job_data(duration=0))

        assert result['ok'] is True
        # 90s -> ceil(90/60) = 2 minutes
        mock_db.update_user_balance.assert_called_once_with(12345, -2)


# ==================== process_job() — debug mode ====================

class TestDebugMode:
    """Test diarization debug output for admin."""

    def test_debug_info_sent_to_owner(self, mock_db, mock_tg, mock_audio):
        """Debug info sent when chat_id == OWNER_ID and debug_mode=True."""
        import handler
        mock_db.get_user.return_value = {
            'balance_minutes': 100,
            'settings': json.dumps({'debug_mode': True}),
        }
        mock_audio.get_diarization_debug.return_value = 'Debug: 2 speakers detected'
        mock_audio.get_audio_duration.return_value = 30.0

        with patch.object(handler, 'get_db_service', return_value=mock_db), \
             patch.object(handler, 'get_telegram_service', return_value=mock_tg), \
             patch.object(handler, 'get_audio_service', return_value=mock_audio), \
             patch('os.remove'):
            # chat_id must equal OWNER_ID (999)
            job = _make_job_data(duration=30)
            job['chat_id'] = '999'
            result = handler.process_job(job)

        assert result['ok'] is True
        # Find debug message (contains <pre>)
        debug_calls = [
            c for c in mock_tg.send_message.call_args_list
            if '<pre>' in str(c)
        ]
        assert len(debug_calls) == 1

    def test_no_debug_for_regular_user(self, patch_services, mock_db, mock_tg, mock_audio):
        """Debug info NOT sent for non-owner user."""
        import handler
        mock_audio.get_diarization_debug.return_value = 'Debug info'

        result = handler.process_job(_make_job_data(duration=30))

        assert result['ok'] is True
        # No debug calls (chat_id=67890 != OWNER_ID=999)
        debug_calls = [
            c for c in mock_tg.send_message.call_args_list
            if '<pre>' in str(c)
        ]
        assert len(debug_calls) == 0
