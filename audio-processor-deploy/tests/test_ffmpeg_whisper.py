import pytest
import os
import subprocess
from telegram_bot_shared.services.audio import AudioService

def test_ffmpeg_whisper_available():
    """Test that FFmpeg 8.0 with Whisper filter is installed"""
    # Skip if ffmpeg not installed (e.g. running locally without custom ffmpeg)
    try:
        subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)
    except FileNotFoundError:
        pytest.skip("FFmpeg not found")

    result = subprocess.run(
        ['ffmpeg', '-filters'],
        capture_output=True,
        text=True
    )
    if 'whisper' not in result.stdout:
        pytest.skip("FFmpeg Whisper filter not found (standard ffmpeg installed?)")
    
    assert 'whisper' in result.stdout, "FFmpeg Whisper filter not found"


def test_whisper_model_exists():
    """Test that Whisper model file exists"""
    model_path = os.getenv('WHISPER_MODEL_PATH', '/opt/whisper/models/ggml-base.bin')
    if not os.path.exists(model_path):
         pytest.skip(f"Whisper model not found at {model_path} (expected in Docker)")
    assert os.path.exists(model_path), f"Whisper model not found at {model_path}"


def test_transcribe_sample_audio():
    """Test transcription on a sample audio file"""
    audio_service = AudioService()

    # Use a test audio file (Russian speech, ~10 seconds)
    test_audio = 'tests/fixtures/sample_russian_10s.mp3'

    if not os.path.exists(test_audio):
        pytest.skip("Test audio file not found")

    # Transcribe
    result = audio_service.transcribe_audio(test_audio)

    # Verify result
    assert result, "Transcription is empty"
    assert len(result) > 5, "Transcription too short"
    assert isinstance(result, str), "Transcription should be string"

    print(f"Transcription result: {result}")


def test_transcribe_empty_audio():
    """Test that empty/silent audio is handled gracefully"""
    audio_service = AudioService()

    test_audio = 'tests/fixtures/silent_10s.mp3'

    if not os.path.exists(test_audio):
        pytest.skip("Test audio file not found")

    # Should raise ValueError for speechless audio
    with pytest.raises(ValueError, match="No speech detected"):
        audio_service.transcribe_audio(test_audio)
