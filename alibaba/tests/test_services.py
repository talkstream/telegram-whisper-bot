#!/usr/bin/env python3
"""
Integration tests for Alibaba Cloud services
Run with: python -m pytest alibaba/tests/test_services.py -v
"""
import os
import sys
import json
import tempfile

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


# ============== Configuration ==============
TABLESTORE_ENDPOINT = os.environ.get('TABLESTORE_ENDPOINT', 'https://twbot-prod.eu-central-1.ots.aliyuncs.com')
TABLESTORE_INSTANCE = os.environ.get('TABLESTORE_INSTANCE', 'twbot-prod')
MNS_ENDPOINT = os.environ.get('MNS_ENDPOINT', 'https://5907469887573677.mns.eu-central-1.aliyuncs.com')
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ALIBABA_ACCESS_KEY = os.environ.get('ALIBABA_ACCESS_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
ALIBABA_SECRET_KEY = os.environ.get('ALIBABA_SECRET_KEY') or os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')


# ============== Tablestore Tests ==============
class TestTablestore:
    """Test Tablestore connectivity and operations"""

    @pytest.fixture
    def tablestore_service(self):
        """Create Tablestore service instance"""
        if not ALIBABA_ACCESS_KEY or not ALIBABA_SECRET_KEY:
            pytest.skip("Alibaba credentials not configured")

        from services.tablestore_service import TablestoreService
        return TablestoreService(
            endpoint=TABLESTORE_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            instance_name=TABLESTORE_INSTANCE
        )

    def test_connection(self, tablestore_service):
        """Test basic Tablestore connection"""
        # Should not raise exception
        assert tablestore_service is not None

    def test_get_user(self, tablestore_service):
        """Test get_user method"""
        # Test with non-existent user
        user = tablestore_service.get_user(999999999)
        # Should return None for non-existent user
        assert user is None or isinstance(user, dict)

    def test_get_user_settings(self, tablestore_service):
        """Test get_user_settings method"""
        settings = tablestore_service.get_user_settings(999999999)
        # Should return None or dict
        assert settings is None or isinstance(settings, dict)


# ============== Balance Update Tests ==============
class TestBalanceUpdate:
    """Test balance update functionality with optimistic locking"""

    TEST_USER_ID = 999999998  # Dedicated test user

    @pytest.fixture
    def tablestore_service(self):
        """Create Tablestore service instance"""
        if not ALIBABA_ACCESS_KEY or not ALIBABA_SECRET_KEY:
            pytest.skip("Alibaba credentials not configured")

        from services.tablestore_service import TablestoreService
        return TablestoreService(
            endpoint=TABLESTORE_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            instance_name=TABLESTORE_INSTANCE
        )

    @pytest.fixture
    def test_user(self, tablestore_service):
        """Create or ensure test user exists with known balance"""
        user = tablestore_service.get_user(self.TEST_USER_ID)
        if not user:
            # Create test user - balance must be integer for Tablestore
            tablestore_service.create_user(self.TEST_USER_ID, {
                'balance_minutes': 100,  # Must be int
                'trial_status': 'test',
                'first_name': 'TestUser'
            })
        else:
            # Reset balance to known value
            tablestore_service.update_user(self.TEST_USER_ID, {'balance_minutes': 100})
        return self.TEST_USER_ID

    def test_balance_deduction(self, tablestore_service, test_user):
        """Test that balance is correctly deducted"""
        # Get initial balance
        user_before = tablestore_service.get_user(test_user)
        initial_balance = float(user_before.get('balance_minutes', 0))

        # Deduct 5 minutes
        result = tablestore_service.update_user_balance(test_user, -5)
        assert result is True, "Balance update should return True"

        # Verify balance was deducted
        user_after = tablestore_service.get_user(test_user)
        new_balance = float(user_after.get('balance_minutes', 0))
        assert new_balance == initial_balance - 5, f"Balance should be {initial_balance - 5}, got {new_balance}"

    def test_balance_addition(self, tablestore_service, test_user):
        """Test that balance can be added"""
        user_before = tablestore_service.get_user(test_user)
        initial_balance = float(user_before.get('balance_minutes', 0))

        # Add 10 minutes
        result = tablestore_service.update_user_balance(test_user, 10)
        assert result is True

        user_after = tablestore_service.get_user(test_user)
        new_balance = float(user_after.get('balance_minutes', 0))
        assert new_balance == initial_balance + 10

    def test_balance_cannot_go_negative(self, tablestore_service, test_user):
        """Test that balance cannot go below zero"""
        # Set balance to 5 (must be int)
        tablestore_service.update_user(test_user, {'balance_minutes': 5})

        # Try to deduct 10
        result = tablestore_service.update_user_balance(test_user, -10)
        assert result is True

        # Balance should be 0, not -5
        user = tablestore_service.get_user(test_user)
        balance = float(user.get('balance_minutes', 0))
        assert balance == 0, f"Balance should be 0, got {balance}"

    def test_balance_update_returns_false_for_nonexistent_user(self, tablestore_service):
        """Test that updating nonexistent user returns False"""
        result = tablestore_service.update_user_balance(777777777, -1)
        assert result is False

    def test_balance_type_consistency(self, tablestore_service, test_user):
        """Test that balance type remains consistent after updates"""
        # Set initial balance as integer
        tablestore_service.update_user(test_user, {'balance_minutes': 50})

        # Update should work even if stored as int
        result = tablestore_service.update_user_balance(test_user, -3)
        assert result is True, "Update should succeed with integer balance"

        user = tablestore_service.get_user(test_user)
        balance = user.get('balance_minutes')
        # Balance should now be float
        assert balance == 47.0 or balance == 47

    def test_multiple_sequential_updates(self, tablestore_service, test_user):
        """Test multiple sequential balance updates"""
        # Reset to known value (must be int)
        tablestore_service.update_user(test_user, {'balance_minutes': 100})

        # Perform 5 sequential deductions
        for i in range(5):
            result = tablestore_service.update_user_balance(test_user, -10)
            assert result is True, f"Update {i+1} should succeed"

        # Verify final balance
        user = tablestore_service.get_user(test_user)
        balance = int(user.get('balance_minutes', 0))
        assert balance == 50, f"Balance should be 50 after 5x10 deductions, got {balance}"


# ============== DashScope ASR Tests ==============
class TestDashScopeASR:
    """Test DashScope ASR (Qwen) functionality"""

    @pytest.fixture
    def audio_service(self):
        """Create Audio service instance"""
        if not DASHSCOPE_API_KEY:
            pytest.skip("DASHSCOPE_API_KEY not configured")

        from services.audio import AudioService
        return AudioService(
            whisper_backend='qwen-asr',
            alibaba_api_key=DASHSCOPE_API_KEY
        )

    def test_service_initialization(self, audio_service):
        """Test AudioService initializes correctly"""
        assert audio_service is not None
        assert audio_service.whisper_backend == 'qwen-asr'

    def test_convert_to_mp3(self, audio_service):
        """Test audio conversion to MP3"""
        # Create a simple test audio file (silent)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            # Write minimal WAV header (silent audio)
            import struct
            # WAV header for 1 second of silence at 44100Hz mono
            sample_rate = 44100
            num_samples = sample_rate  # 1 second
            bytes_per_sample = 2

            f.write(b'RIFF')
            f.write(struct.pack('<I', 36 + num_samples * bytes_per_sample))
            f.write(b'WAVEfmt ')
            f.write(struct.pack('<I', 16))  # Subchunk1Size
            f.write(struct.pack('<H', 1))   # AudioFormat (PCM)
            f.write(struct.pack('<H', 1))   # NumChannels
            f.write(struct.pack('<I', sample_rate))  # SampleRate
            f.write(struct.pack('<I', sample_rate * bytes_per_sample))  # ByteRate
            f.write(struct.pack('<H', bytes_per_sample))  # BlockAlign
            f.write(struct.pack('<H', 16))  # BitsPerSample
            f.write(b'data')
            f.write(struct.pack('<I', num_samples * bytes_per_sample))
            f.write(b'\x00' * num_samples * bytes_per_sample)  # Silent audio
            test_file = f.name

        try:
            result = audio_service.convert_to_mp3(test_file)
            assert result is not None
            assert os.path.exists(result)
            assert result.endswith('.mp3')
            os.unlink(result)
        finally:
            os.unlink(test_file)


# ============== Qwen LLM Tests ==============
class TestQwenLLM:
    """Test Qwen LLM formatting functionality"""

    @pytest.fixture
    def audio_service(self):
        """Create Audio service instance"""
        if not DASHSCOPE_API_KEY:
            pytest.skip("DASHSCOPE_API_KEY not configured")

        from services.audio import AudioService
        return AudioService(alibaba_api_key=DASHSCOPE_API_KEY)

    def test_format_short_text(self, audio_service):
        """Test that short text is returned unchanged"""
        short_text = "Привет мир"
        result = audio_service.format_text_with_qwen(short_text)
        assert result == short_text

    def test_format_text_with_qwen(self, audio_service):
        """Test Qwen LLM text formatting"""
        test_text = """
        привет это тестовая транскрипция которая содержит несколько предложений
        без знаков препинания и с некоторыми ошибками распознавания речи
        надеюсь что кьен справится с форматированием этого текста
        """

        result = audio_service.format_text_with_qwen(test_text.strip())

        # Result should be non-empty
        assert result is not None
        assert len(result) > 0
        # Should contain some punctuation (formatting was applied)
        assert any(c in result for c in '.!?,')


# ============== MNS Queue Tests ==============
class TestMNSQueue:
    """Test MNS message queue functionality"""

    @pytest.fixture
    def mns_service(self):
        """Create MNS service instance"""
        if not ALIBABA_ACCESS_KEY or not ALIBABA_SECRET_KEY:
            pytest.skip("Alibaba credentials not configured")

        from services.mns_service import MNSService
        return MNSService(
            endpoint=MNS_ENDPOINT,
            access_key_id=ALIBABA_ACCESS_KEY,
            access_key_secret=ALIBABA_SECRET_KEY,
            queue_name='telegram-whisper-bot-prod-audio-jobs'
        )

    def test_connection(self, mns_service):
        """Test MNS connection"""
        assert mns_service is not None

    def test_get_queue_attributes(self, mns_service):
        """Test getting queue attributes"""
        attrs = mns_service.get_queue_attributes()
        assert attrs is not None
        assert 'active_messages' in attrs


# ============== Telegram API Tests ==============
class TestTelegramAPI:
    """Test Telegram API functionality"""

    @pytest.fixture
    def telegram_service(self):
        """Create Telegram service instance"""
        if not TELEGRAM_BOT_TOKEN:
            pytest.skip("TELEGRAM_BOT_TOKEN not configured")

        from services.telegram import TelegramService
        return TelegramService(TELEGRAM_BOT_TOKEN)

    def test_connection(self, telegram_service):
        """Test Telegram API connection"""
        assert telegram_service is not None

    def test_get_me(self, telegram_service):
        """Test getMe API call"""
        import requests
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        )
        data = response.json()
        assert data['ok'] is True
        assert 'result' in data


# ============== E2E Webhook Test ==============
class TestWebhookE2E:
    """End-to-end webhook tests"""

    def test_webhook_start_command(self):
        """Test /start command processing"""
        import requests

        webhook_url = os.environ.get(
            'WEBHOOK_URL',
            'https://webhook-handler-telegrabot-prod-zmdupczvfj.eu-central-1.fcapp.run/'
        )

        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 123456789, "is_bot": False, "first_name": "Test"},
                "chat": {"id": 123456789, "type": "private"},
                "date": 1706900000,
                "text": "/start"
            }
        }

        response = requests.post(webhook_url, json=payload)
        data = response.json()

        assert response.status_code == 200
        assert data.get('ok') is True
        assert data.get('result') == 'start'

    def test_webhook_balance_command(self):
        """Test /balance command processing"""
        import requests

        webhook_url = os.environ.get(
            'WEBHOOK_URL',
            'https://webhook-handler-telegrabot-prod-zmdupczvfj.eu-central-1.fcapp.run/'
        )

        payload = {
            "update_id": 2,
            "message": {
                "message_id": 2,
                "from": {"id": 123456789, "is_bot": False, "first_name": "Test"},
                "chat": {"id": 123456789, "type": "private"},
                "date": 1706900000,
                "text": "/balance"
            }
        }

        response = requests.post(webhook_url, json=payload)
        data = response.json()

        assert response.status_code == 200
        assert data.get('ok') is True


# ============== Run Tests ==============
if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
