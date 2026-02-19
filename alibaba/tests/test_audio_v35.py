#!/usr/bin/env python3
"""
Unit tests for v3.5.0 audio pipeline refactoring.
Tests: adaptive bitrate, prepare_audio_for_asr, split_audio_chunks,
       chunked transcription, is_chunked prompt, document handler,
       user-friendly errors, duration detection.

Run with: cd alibaba && python -m pytest tests/test_audio_v35.py -v
"""
import os
import sys
import json
import struct
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

from audio import AudioService


# ============== Fixtures ==============

@pytest.fixture
def audio_service():
    """Create AudioService with qwen-asr backend."""
    return AudioService(whisper_backend='qwen-asr', alibaba_api_key='test-key')


@pytest.fixture
def silent_wav(tmp_path):
    """Create a minimal valid WAV file (1 second silence)."""
    filepath = str(tmp_path / "test.wav")
    sample_rate = 16000
    num_samples = sample_rate
    bytes_per_sample = 2

    with open(filepath, 'wb') as f:
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + num_samples * bytes_per_sample))
        f.write(b'WAVEfmt ')
        f.write(struct.pack('<I', 16))
        f.write(struct.pack('<H', 1))   # PCM
        f.write(struct.pack('<H', 1))   # mono
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * bytes_per_sample))
        f.write(struct.pack('<H', bytes_per_sample))
        f.write(struct.pack('<H', 16))
        f.write(b'data')
        f.write(struct.pack('<I', num_samples * bytes_per_sample))
        f.write(b'\x00' * num_samples * bytes_per_sample)

    return filepath


# ============== Constants Tests ==============

class TestConstants:
    """Test that v3.5.0 constants are correctly defined."""

    def test_asr_max_duration(self, audio_service):
        assert audio_service.ASR_MAX_DURATION == 180

    def test_asr_max_chunk_duration(self, audio_service):
        assert audio_service.ASR_MAX_CHUNK_DURATION == 150

    def test_audio_bitrate(self, audio_service):
        assert audio_service.AUDIO_BITRATE == '48k'

    def test_bitrate_tiers_exist(self, audio_service):
        assert hasattr(audio_service, 'BITRATE_TIERS')
        assert len(audio_service.BITRATE_TIERS) >= 3


# ============== Adaptive Bitrate Tests ==============

class TestAdaptiveBitrate:
    """Test _select_bitrate adaptive compression."""

    def test_short_audio_ultra_light(self, audio_service):
        bitrate, sample_rate, tier = audio_service._select_bitrate(5)
        assert bitrate == '24k'
        assert tier == 'ultra-light'

    def test_medium_audio_standard(self, audio_service):
        bitrate, sample_rate, tier = audio_service._select_bitrate(120)
        assert bitrate == '48k'
        assert tier == 'standard'

    def test_long_audio_compressed(self, audio_service):
        bitrate, sample_rate, tier = audio_service._select_bitrate(900)
        assert bitrate == '32k'
        assert tier == 'compressed'

    def test_very_long_audio_compressed(self, audio_service):
        bitrate, sample_rate, tier = audio_service._select_bitrate(2400)
        assert bitrate == '32k'
        assert tier == 'compressed'

    def test_exceeding_max_tier(self, audio_service):
        """Audio longer than any tier still gets fallback."""
        bitrate, sample_rate, tier = audio_service._select_bitrate(99999)
        assert bitrate == '32k'
        assert sample_rate == '16000'

    def test_all_tiers_use_16khz(self, audio_service):
        """All tiers must use 16kHz for ASR quality."""
        for max_dur, bitrate, sample_rate, label in audio_service.BITRATE_TIERS:
            assert sample_rate == '16000', f"Tier {label} uses {sample_rate}, expected 16000"

    def test_edge_case_exactly_10_seconds(self, audio_service):
        bitrate, _, tier = audio_service._select_bitrate(10)
        assert tier == 'ultra-light'

    def test_edge_case_11_seconds(self, audio_service):
        bitrate, _, tier = audio_service._select_bitrate(11)
        assert tier == 'standard'


# ============== convert_to_mp3 Tests ==============

class TestConvertToMp3:
    """Test convert_to_mp3 with FFmpeg flags."""

    @patch('os.path.getsize', return_value=1000)
    @patch('subprocess.run')
    @patch.object(AudioService, 'get_audio_duration', return_value=30.0)
    def test_has_vn_flag(self, mock_duration, mock_run, mock_size, audio_service, tmp_path):
        """convert_to_mp3 must include -vn flag."""
        mock_run.return_value = MagicMock(returncode=0)
        input_path = str(tmp_path / "input.m4a")
        output_path = str(tmp_path / "output.mp3")
        open(input_path, 'w').close()

        audio_service.convert_to_mp3(input_path, output_path)

        args = mock_run.call_args[0][0]
        assert '-vn' in args

    @patch('os.path.getsize', return_value=1000)
    @patch('subprocess.run')
    @patch.object(AudioService, 'get_audio_duration', return_value=30.0)
    def test_has_libmp3lame_codec(self, mock_duration, mock_run, mock_size, audio_service, tmp_path):
        """convert_to_mp3 must use libmp3lame codec."""
        mock_run.return_value = MagicMock(returncode=0)
        input_path = str(tmp_path / "input.m4a")
        output_path = str(tmp_path / "output.mp3")
        open(input_path, 'w').close()

        audio_service.convert_to_mp3(input_path, output_path)

        args = mock_run.call_args[0][0]
        assert 'libmp3lame' in args

    @patch('os.path.getsize', return_value=1000)
    @patch('subprocess.run')
    @patch.object(AudioService, 'get_audio_duration', return_value=5.0)
    def test_short_audio_uses_24k(self, mock_duration, mock_run, mock_size, audio_service, tmp_path):
        """Short audio (<10s) should use 24k bitrate."""
        mock_run.return_value = MagicMock(returncode=0)
        input_path = str(tmp_path / "input.wav")
        output_path = str(tmp_path / "output.mp3")
        open(input_path, 'w').close()

        audio_service.convert_to_mp3(input_path, output_path)

        args = mock_run.call_args[0][0]
        bitrate_idx = args.index('-b:a') + 1
        assert args[bitrate_idx] == '24k'

    @patch('os.path.getsize', return_value=1000)
    @patch('subprocess.run')
    @patch.object(AudioService, 'get_audio_duration', return_value=1200.0)
    def test_long_audio_uses_32k(self, mock_duration, mock_run, mock_size, audio_service, tmp_path):
        """Long audio (>10min) should use 32k bitrate (compressed tier)."""
        mock_run.return_value = MagicMock(returncode=0)
        input_path = str(tmp_path / "input.wav")
        output_path = str(tmp_path / "output.mp3")
        open(input_path, 'w').close()

        audio_service.convert_to_mp3(input_path, output_path)

        args = mock_run.call_args[0][0]
        bitrate_idx = args.index('-b:a') + 1
        assert args[bitrate_idx] == '32k'


# ============== prepare_audio_for_asr Tests ==============

class TestPrepareAudioForAsr:
    """Test prepare_audio_for_asr pipeline."""

    @patch.object(AudioService, 'convert_to_mp3', return_value='/tmp/converted.mp3')
    @patch.object(AudioService, 'is_video_file', return_value=False)
    def test_audio_file_direct_conversion(self, mock_video, mock_convert, audio_service):
        """Regular audio goes directly to convert_to_mp3."""
        result = audio_service.prepare_audio_for_asr('/tmp/test.ogg')
        assert result == '/tmp/converted.mp3'
        mock_convert.assert_called_once_with('/tmp/test.ogg')

    @patch.object(AudioService, 'convert_to_mp3', return_value='/tmp/converted.mp3')
    @patch.object(AudioService, 'extract_audio_from_video', return_value='/tmp/extracted.mp3')
    @patch.object(AudioService, 'is_video_file', return_value=True)
    @patch('os.path.exists', return_value=True)
    @patch('os.remove')
    def test_video_file_extracts_then_converts(self, mock_rm, mock_exists,
                                                mock_video, mock_extract, mock_convert,
                                                audio_service):
        """Video file extracts audio then converts."""
        result = audio_service.prepare_audio_for_asr('/tmp/test.mp4')
        assert result == '/tmp/converted.mp3'
        mock_extract.assert_called_once_with('/tmp/test.mp4')
        mock_convert.assert_called_once_with('/tmp/extracted.mp3')

    @patch.object(AudioService, 'extract_audio_from_video', return_value=None)
    @patch.object(AudioService, 'is_video_file', return_value=True)
    def test_video_extraction_failure(self, mock_video, mock_extract, audio_service):
        """Returns None if video audio extraction fails."""
        result = audio_service.prepare_audio_for_asr('/tmp/test.mp4')
        assert result is None

    @patch.object(AudioService, 'convert_to_mp3', return_value=None)
    @patch.object(AudioService, 'is_video_file', return_value=False)
    def test_conversion_failure(self, mock_video, mock_convert, audio_service):
        """Returns None if MP3 conversion fails."""
        result = audio_service.prepare_audio_for_asr('/tmp/test.ogg')
        assert result is None


# ============== split_audio_chunks Tests ==============

class TestSplitAudioChunks:
    """Test split_audio_chunks."""

    @patch.object(AudioService, 'get_audio_duration', return_value=60.0)
    def test_short_audio_no_split(self, mock_duration, audio_service):
        """Audio shorter than chunk_duration returns original path."""
        result = audio_service.split_audio_chunks('/tmp/short.mp3')
        assert result == ['/tmp/short.mp3']

    @patch.object(AudioService, 'get_audio_duration', return_value=150.0)
    def test_exact_chunk_duration_no_split(self, mock_duration, audio_service):
        """Audio exactly at chunk_duration should NOT split."""
        result = audio_service.split_audio_chunks('/tmp/exact.mp3')
        assert result == ['/tmp/exact.mp3']

    @patch('subprocess.run')
    @patch('os.path.getsize', return_value=1000)
    @patch('os.path.exists', return_value=True)
    @patch.object(AudioService, 'get_audio_duration', return_value=320.0)
    def test_splits_into_correct_count(self, mock_duration, mock_exists,
                                        mock_size, mock_run, audio_service):
        """320s audio with 150s chunks should produce 3 chunks."""
        mock_run.return_value = MagicMock(returncode=0)

        result = audio_service.split_audio_chunks('/tmp/long.mp3')
        # 320 / 150 = 2.13 → 3 chunks (0-150, 150-300, 300-320)
        assert len(result) == 3

    @patch('subprocess.run', side_effect=Exception("FFmpeg error"))
    @patch.object(AudioService, 'get_audio_duration', return_value=320.0)
    def test_split_failure_returns_original(self, mock_duration, mock_run, audio_service):
        """On FFmpeg failure, return original file as fallback."""
        result = audio_service.split_audio_chunks('/tmp/long.mp3')
        assert result == ['/tmp/long.mp3']


# ============== Chunked Transcription Tests ==============

class TestChunkedTranscription:
    """Test transcribe_with_qwen_asr routing and _transcribe_chunked."""

    @patch.object(AudioService, '_transcribe_single_qwen_asr', return_value="Hello world")
    @patch.object(AudioService, 'get_audio_duration', return_value=60.0)
    def test_short_audio_no_chunking(self, mock_duration, mock_single, audio_service):
        """Short audio goes to _transcribe_single_qwen_asr directly."""
        result = audio_service.transcribe_with_qwen_asr('/tmp/short.mp3')
        assert result == "Hello world"
        mock_single.assert_called_once()

    @patch.object(AudioService, '_transcribe_chunked', return_value="Chunked text")
    @patch.object(AudioService, 'get_audio_duration', return_value=200.0)
    def test_long_audio_triggers_chunking(self, mock_duration, mock_chunked, audio_service):
        """Audio > ASR_MAX_CHUNK_DURATION triggers _transcribe_chunked."""
        result = audio_service.transcribe_with_qwen_asr('/tmp/long.mp3')
        assert result == "Chunked text"
        mock_chunked.assert_called_once()

    @patch.object(AudioService, '_transcribe_single_qwen_asr')
    @patch.object(AudioService, 'split_audio_chunks')
    def test_chunked_concatenates_results(self, mock_split, mock_single, audio_service):
        """_transcribe_chunked concatenates results from all chunks."""
        mock_split.return_value = ['/tmp/c0.mp3', '/tmp/c1.mp3', '/tmp/c2.mp3']
        mock_single.side_effect = ["Part one.", "Part two.", "Part three."]

        with patch('os.path.exists', return_value=False):
            result = audio_service._transcribe_chunked('/tmp/test.mp3', 'ru', 400.0)

        assert result == "Part one. Part two. Part three."
        assert mock_single.call_count == 3

    @patch.object(AudioService, '_transcribe_single_qwen_asr')
    @patch.object(AudioService, 'split_audio_chunks')
    def test_chunked_progress_callback(self, mock_split, mock_single, audio_service):
        """_transcribe_chunked calls progress_callback for each chunk."""
        mock_split.return_value = ['/tmp/c0.mp3', '/tmp/c1.mp3']
        mock_single.side_effect = ["First", "Second"]
        callback = MagicMock()

        with patch('os.path.exists', return_value=False):
            audio_service._transcribe_chunked('/tmp/test.mp3', 'ru', 300.0, callback)

        callback.assert_any_call(1, 2)
        callback.assert_any_call(2, 2)

    @patch.object(AudioService, '_transcribe_single_qwen_asr')
    @patch.object(AudioService, 'split_audio_chunks')
    def test_chunked_graceful_degradation(self, mock_split, mock_single, audio_service):
        """If some chunks fail (<50%), return partial result."""
        mock_split.return_value = ['/tmp/c0.mp3', '/tmp/c1.mp3', '/tmp/c2.mp3', '/tmp/c3.mp3']
        mock_single.side_effect = [
            "Part one.",
            RuntimeError("API error"),
            "Part three.",
            "Part four."
        ]

        with patch('os.path.exists', return_value=False):
            result = audio_service._transcribe_chunked('/tmp/test.mp3', 'ru', 600.0)

        assert "Part one." in result
        assert "Part three." in result
        assert "Part four." in result

    @patch.object(AudioService, '_transcribe_single_qwen_asr')
    @patch.object(AudioService, 'split_audio_chunks')
    def test_chunked_too_many_failures_raises(self, mock_split, mock_single, audio_service):
        """If >50% chunks fail, raise RuntimeError."""
        mock_split.return_value = ['/tmp/c0.mp3', '/tmp/c1.mp3', '/tmp/c2.mp3']
        mock_single.side_effect = [
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
            "Part three.",
        ]

        with patch('os.path.exists', return_value=False):
            with pytest.raises(RuntimeError, match="Too many chunks failed"):
                audio_service._transcribe_chunked('/tmp/test.mp3', 'ru', 450.0)

    @patch.object(AudioService, '_transcribe_single_qwen_asr')
    @patch.object(AudioService, 'split_audio_chunks')
    def test_chunked_all_empty_raises(self, mock_split, mock_single, audio_service):
        """If all chunks return empty, raise ValueError."""
        mock_split.return_value = ['/tmp/c0.mp3']
        mock_single.return_value = ""

        with patch('os.path.exists', return_value=False):
            with pytest.raises(ValueError, match="Transcription empty"):
                audio_service._transcribe_chunked('/tmp/test.mp3', 'ru', 200.0)


# ============== transcribe_audio with progress_callback ==============

class TestTranscribeAudioCallback:
    """Test that transcribe_audio passes progress_callback."""

    @patch.object(AudioService, 'transcribe_with_qwen_asr', return_value="text")
    def test_callback_passed_to_qwen(self, mock_qwen, audio_service):
        callback = MagicMock()
        audio_service.transcribe_audio('/tmp/test.mp3', progress_callback=callback)
        mock_qwen.assert_called_once_with('/tmp/test.mp3', 'ru', callback)


# ============== format_text_with_qwen is_chunked Tests ==============

class TestFormatWithChunkedFlag:
    """Test is_chunked parameter in LLM formatting."""

    @patch('requests.post')
    def test_qwen_prompt_without_chunked(self, mock_post, audio_service):
        """Without is_chunked, prompt should not contain stitching instruction."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'output': {'text': 'Formatted text.'}
        }
        mock_post.return_value = mock_response

        text = "Это достаточно длинный текст для форматирования чтобы превысить минимальный порог в десять слов для обработки."
        audio_service.format_text_with_qwen(text, is_chunked=False)

        sent_payload = mock_post.call_args[1]['json']
        prompt = sent_payload['input']['messages'][0]['content']
        assert 'артефакты склейки' not in prompt

    @patch('requests.post')
    def test_qwen_prompt_with_chunked(self, mock_post, audio_service):
        """With is_chunked=True, prompt must contain stitching instruction."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'output': {'text': 'Formatted text.'}
        }
        mock_post.return_value = mock_response

        text = "Это достаточно длинный текст для форматирования чтобы превысить минимальный порог в десять слов для обработки."
        audio_service.format_text_with_qwen(text, is_chunked=True)

        sent_payload = mock_post.call_args[1]['json']
        prompt = sent_payload['input']['messages'][0]['content']
        assert 'артефакты склейки' in prompt

    @patch('requests.post')
    def test_qwen_prompt_has_toponym_instruction(self, mock_post, audio_service):
        """Prompt must contain toponym instruction regardless of is_chunked."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'output': {'text': 'Formatted text.'}
        }
        mock_post.return_value = mock_response

        text = "Это достаточно длинный текст для форматирования чтобы превысить минимальный порог в десять слов для обработки."
        audio_service.format_text_with_qwen(text, is_chunked=False)

        sent_payload = mock_post.call_args[1]['json']
        prompt = sent_payload['input']['messages'][0]['content']
        assert 'топонимов' in prompt
        assert 'Таиланда' in prompt

    def test_short_text_skips_llm(self, audio_service):
        """Text with <10 words should be returned unchanged."""
        short = "Привет мир"
        result = audio_service.format_text_with_qwen(short, is_chunked=True)
        assert result == short


# ============== Document Handler Tests ==============

class TestDocumentHandler:
    """Test document handling in webhook handler."""

    def test_audio_document_detected(self):
        """Message with audio/* document should be handled."""
        message = {
            'chat': {'id': 123},
            'from': {'id': 456, 'first_name': 'Test'},
            'document': {
                'file_id': 'doc_file_123',
                'mime_type': 'audio/mp4',
                'file_name': 'recording.m4a'
            }
        }

        # Verify document with audio MIME is detected
        doc = message['document']
        mime = doc.get('mime_type', '')
        assert mime.startswith('audio/')

    def test_video_document_detected(self):
        """Message with video/* document should be handled."""
        message = {
            'document': {
                'file_id': 'doc_file_456',
                'mime_type': 'video/mp4',
                'file_name': 'clip.mp4'
            }
        }
        doc = message['document']
        mime = doc.get('mime_type', '')
        assert mime.startswith('video/')

    def test_non_media_document_ignored(self):
        """Message with non-media document should NOT be handled."""
        message = {
            'document': {
                'file_id': 'doc_file_789',
                'mime_type': 'application/pdf',
                'file_name': 'document.pdf'
            }
        }
        doc = message['document']
        mime = doc.get('mime_type', '')
        assert not mime.startswith('audio/') and not mime.startswith('video/')

    def test_document_duration_defaults_to_zero(self):
        """Document messages have duration=0 (Telegram doesn't provide it)."""
        doc = {'file_id': 'abc', 'mime_type': 'audio/mp4'}
        duration = doc.get('duration', 0)
        assert duration == 0


# ============== User-Friendly Error Messages ==============

class TestUserFriendlyErrors:
    """Test error message mapping logic."""

    def _get_user_msg(self, error_text):
        """Simulate error mapping logic from handlers."""
        error_str = str(error_text).lower()
        if 'invalidparameter' in error_str or 'duration' in error_str:
            return "Аудио слишком длинное для обработки. Попробуйте отправить файл короче 60 минут."
        elif 'timeout' in error_str:
            return "Обработка заняла слишком много времени. Попробуйте файл поменьше."
        elif 'transcription empty' in error_str or 'no speech' in error_str:
            return "Не удалось распознать речь. Проверьте качество аудио."
        else:
            return "Произошла ошибка при обработке аудио. Попробуйте позже."

    def test_invalid_parameter_error(self):
        msg = self._get_user_msg("API error: InvalidParameter.Algo - duration too long")
        assert "длинное" in msg

    def test_timeout_error(self):
        msg = self._get_user_msg("Connection timeout after 120s")
        assert "времени" in msg

    def test_empty_transcription_error(self):
        msg = self._get_user_msg("Transcription empty")
        assert "распознать речь" in msg

    def test_no_speech_error(self):
        msg = self._get_user_msg("No speech detected in audio")
        assert "распознать речь" in msg

    def test_generic_error(self):
        msg = self._get_user_msg("Some unknown error occurred")
        assert "Попробуйте позже" in msg

    def test_case_insensitive(self):
        msg = self._get_user_msg("INVALIDPARAMETER something")
        assert "длинное" in msg


# ============== Edge Cases ==============

class TestEdgeCases:
    """Test edge cases in the audio pipeline."""

    @patch.object(AudioService, '_transcribe_single_qwen_asr', return_value="text")
    @patch.object(AudioService, 'get_audio_duration', return_value=150.0)
    def test_exactly_chunk_limit_no_chunking(self, mock_dur, mock_single, audio_service):
        """Audio at exactly ASR_MAX_CHUNK_DURATION (150s) should NOT chunk."""
        result = audio_service.transcribe_with_qwen_asr('/tmp/test.mp3')
        assert result == "text"
        mock_single.assert_called_once()

    @patch.object(AudioService, '_transcribe_chunked', return_value="chunked")
    @patch.object(AudioService, 'get_audio_duration', return_value=151.0)
    def test_just_over_chunk_limit_triggers_chunking(self, mock_dur, mock_chunked, audio_service):
        """Audio at 151s (just over limit) should trigger chunking."""
        result = audio_service.transcribe_with_qwen_asr('/tmp/test.mp3')
        assert result == "chunked"
        mock_chunked.assert_called_once()

    def test_validate_audio_file_too_large(self, audio_service):
        valid, msg = audio_service.validate_audio_file(25 * 1024 * 1024, 60)
        assert valid is False
        assert "большой" in msg

    def test_validate_audio_file_too_long(self, audio_service):
        valid, msg = audio_service.validate_audio_file(1024, 7200)
        assert valid is False
        assert "длинное" in msg

    def test_validate_audio_file_ok(self, audio_service):
        valid, msg = audio_service.validate_audio_file(1024 * 1024, 300)
        assert valid is True
        assert msg is None


# ============== Integration: convert_to_mp3 with real FFmpeg ==============

class TestConvertToMp3Integration:
    """Integration test: convert_to_mp3 with real FFmpeg (if available)."""

    def test_convert_silent_wav_to_mp3(self, audio_service, silent_wav):
        """Convert a real WAV file to MP3."""
        result = audio_service.convert_to_mp3(silent_wav)
        if result is None:
            pytest.skip("FFmpeg not available")

        assert os.path.exists(result)
        assert result.endswith('.mp3')
        assert os.path.getsize(result) > 0
        os.unlink(result)

    def test_adaptive_bitrate_applied(self, audio_service, silent_wav):
        """Verify that short audio gets 24k bitrate (ultra-light tier)."""
        # silent_wav is 1 second, so should use ultra-light tier
        result = audio_service.convert_to_mp3(silent_wav)
        if result is None:
            pytest.skip("FFmpeg not available")

        # File should be very small due to 24k bitrate
        size = os.path.getsize(result)
        assert size < 10000  # 1 second at 24k should be well under 10KB
        os.unlink(result)


# ============== send_long_message Tests ==============

class TestSendLongMessage:
    """Test TelegramService.send_long_message splitting."""

    @pytest.fixture
    def tg_service(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))
        from telegram import TelegramService
        return TelegramService('fake-token')

    @patch('requests.Session.post')
    def test_short_message_no_split(self, mock_post, tg_service):
        """Message <=4096 chars should be sent as single message."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        tg_service.send_long_message(123, "Short text")
        assert mock_post.call_count == 1

    @patch('requests.Session.post')
    def test_long_message_splits(self, mock_post, tg_service):
        """Message >4096 chars should be split into multiple messages."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        # Create text > 4096 chars with paragraph breaks
        long_text = ("Абзац один. " * 50 + "\n\n") * 10  # ~6200 chars
        tg_service.send_long_message(123, long_text)
        assert mock_post.call_count >= 2

    @patch('requests.Session.post')
    def test_split_respects_4096_limit(self, mock_post, tg_service):
        """Each split part must be <=4096 chars."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        long_text = "Слово " * 2000  # ~12000 chars
        tg_service.send_long_message(123, long_text)

        for call_args in mock_post.call_args_list:
            payload = call_args[1].get('json', call_args[0][1] if len(call_args[0]) > 1 else {})
            if 'text' in payload:
                assert len(payload['text']) <= 4096

    def test_max_message_length_constant(self, tg_service):
        assert tg_service.MAX_MESSAGE_LENGTH == 4096


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
