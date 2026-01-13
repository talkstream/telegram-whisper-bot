import pytest
import os

@pytest.fixture
def test_env():
    """Set up test environment variables"""
    os.environ['GCP_PROJECT'] = 'test-project'
    os.environ['WHISPER_MODEL_PATH'] = '/opt/whisper/models/ggml-base.bin'
    os.environ['REDIS_HOST'] = 'localhost'
    yield
    # Cleanup

@pytest.fixture
def sample_audio():
    """Provide path to test audio file"""
    return 'tests/fixtures/sample_russian_10s.mp3'
