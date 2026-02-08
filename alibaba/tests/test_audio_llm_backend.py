#!/usr/bin/env python3
"""
Unit tests for LLM backend routing and AssemblyAI LLM Gateway.
Tests: router, AssemblyAI success/error/auth, fallback chains.

Run with: cd alibaba && python -m pytest tests/test_audio_llm_backend.py -v
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Add shared to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

from audio import AudioService


LONG_TEXT = (
    "Это достаточно длинный текст для форматирования "
    "чтобы превысить минимальный порог в десять слов для обработки"
)


@pytest.fixture
def audio_service():
    """Create AudioService with qwen-asr backend."""
    return AudioService(whisper_backend='qwen-asr', alibaba_api_key='test-key')


# ============== format_text_with_assemblyai Tests ==============

class TestFormatTextWithAssemblyAI:
    """Test AssemblyAI LLM Gateway formatting."""

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-aai-key'})
    def test_success_path(self, mock_post):
        """Successful API call returns formatted text."""
        service = AudioService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Formatted text.'}}]
        }
        mock_post.return_value = mock_resp

        result = service.format_text_with_assemblyai(LONG_TEXT)
        assert result == 'Formatted text.'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-aai-key'})
    def test_payload_format(self, mock_post):
        """Verify payload: model, messages, max_tokens."""
        service = AudioService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'OK.'}}]
        }
        mock_post.return_value = mock_resp

        service.format_text_with_assemblyai(LONG_TEXT)

        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]['json']
        assert payload['model'] == 'gemini-3-flash-preview'
        assert payload['max_tokens'] == 8192
        assert len(payload['messages']) == 1
        assert payload['messages'][0]['role'] == 'user'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'my-secret-key'})
    def test_auth_header_raw_key(self, mock_post):
        """Auth header should be raw key (no Bearer prefix)."""
        service = AudioService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'OK.'}}]
        }
        mock_post.return_value = mock_resp

        service.format_text_with_assemblyai(LONG_TEXT)

        call_kwargs = mock_post.call_args
        headers = call_kwargs[1].get('headers') or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]['headers']
        assert headers['Authorization'] == 'my-secret-key'
        assert 'Bearer' not in headers['Authorization']

    def test_short_text_skip(self):
        """Text with <10 words should be returned unchanged."""
        service = AudioService()
        short = "Привет мир"
        result = service.format_text_with_assemblyai(short)
        assert result == short

    @patch.dict(os.environ, {}, clear=False)
    def test_missing_api_key_fallback_to_qwen(self):
        """Without ASSEMBLYAI_API_KEY, should fallback to Qwen."""
        service = AudioService(alibaba_api_key='test-key')
        # Remove key if present
        os.environ.pop('ASSEMBLYAI_API_KEY', None)

        with patch.object(service, 'format_text_with_qwen', return_value='qwen result') as mock_qwen:
            result = service.format_text_with_assemblyai(LONG_TEXT)
            mock_qwen.assert_called_once()
            assert result == 'qwen result'

    @patch.dict(os.environ, {}, clear=False)
    def test_missing_api_key_as_fallback_returns_original(self):
        """Without ASSEMBLYAI_API_KEY and _is_fallback=True, return original."""
        service = AudioService()
        os.environ.pop('ASSEMBLYAI_API_KEY', None)

        result = service.format_text_with_assemblyai(LONG_TEXT, _is_fallback=True)
        assert result == LONG_TEXT

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_api_error_fallback_to_qwen(self, mock_post):
        """API error (429/500) should fallback to Qwen."""
        service = AudioService(alibaba_api_key='test-key')
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = 'Rate limited'
        mock_post.return_value = mock_resp

        with patch.object(service, 'format_text_with_qwen', return_value='qwen result') as mock_qwen:
            result = service.format_text_with_assemblyai(LONG_TEXT)
            mock_qwen.assert_called_once()
            assert result == 'qwen result'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_empty_response_fallback(self, mock_post):
        """Empty response should fallback to Qwen."""
        service = AudioService(alibaba_api_key='test-key')
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': ''}}]
        }
        mock_post.return_value = mock_resp

        with patch.object(service, 'format_text_with_qwen', return_value='qwen result') as mock_qwen:
            result = service.format_text_with_assemblyai(LONG_TEXT)
            mock_qwen.assert_called_once()
            assert result == 'qwen result'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_code_tags_cleanup(self, mock_post):
        """Code tags should be removed when use_code_tags=False."""
        service = AudioService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': '<code>Formatted text.</code>'}}]
        }
        mock_post.return_value = mock_resp

        result = service.format_text_with_assemblyai(LONG_TEXT, use_code_tags=False)
        assert '<code>' not in result
        assert result == 'Formatted text.'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_metrics_logging(self, mock_post):
        """Metrics should be logged with 'assemblyai-llm' key."""
        service = AudioService()
        mock_metrics = MagicMock()
        service.metrics_service = mock_metrics
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'choices': [{'message': {'content': 'Formatted.'}}]
        }
        mock_post.return_value = mock_resp

        service.format_text_with_assemblyai(LONG_TEXT)
        mock_metrics.log_api_call.assert_called_once()
        call_args = mock_metrics.log_api_call.call_args[0]
        assert call_args[0] == 'assemblyai-llm'


# ============== format_text_with_llm Router Tests ==============

class TestFormatTextWithLLMRouter:
    """Test LLM backend routing."""

    @patch.dict(os.environ, {}, clear=False)
    def test_default_routes_to_qwen(self, audio_service):
        """No LLM_BACKEND env var → Qwen."""
        os.environ.pop('LLM_BACKEND', None)

        with patch.object(audio_service, 'format_text_with_qwen', return_value='qwen') as mock_q:
            result = audio_service.format_text_with_llm(LONG_TEXT)
            mock_q.assert_called_once()
            assert result == 'qwen'

    @patch.dict(os.environ, {'LLM_BACKEND': 'assemblyai'})
    def test_assemblyai_backend(self, audio_service):
        """LLM_BACKEND=assemblyai → AssemblyAI."""
        with patch.object(audio_service, 'format_text_with_assemblyai', return_value='aai') as mock_a:
            result = audio_service.format_text_with_llm(LONG_TEXT)
            mock_a.assert_called_once()
            assert result == 'aai'

    @patch.dict(os.environ, {'LLM_BACKEND': 'invalid'})
    def test_invalid_backend_defaults_to_qwen(self, audio_service):
        """Invalid LLM_BACKEND → Qwen (safe default)."""
        with patch.object(audio_service, 'format_text_with_qwen', return_value='qwen') as mock_q:
            result = audio_service.format_text_with_llm(LONG_TEXT)
            mock_q.assert_called_once()
            assert result == 'qwen'

    @patch.dict(os.environ, {}, clear=False)
    def test_all_params_forwarded(self, audio_service):
        """All parameters should be forwarded to backend."""
        os.environ.pop('LLM_BACKEND', None)

        with patch.object(audio_service, 'format_text_with_qwen', return_value='ok') as mock_q:
            audio_service.format_text_with_llm(
                LONG_TEXT, use_code_tags=True, use_yo=False,
                is_chunked=True, is_dialogue=True)
            mock_q.assert_called_once_with(
                LONG_TEXT, True, False, True, True)

    @patch.dict(os.environ, {'LLM_BACKEND': 'qwen'})
    def test_backend_param_overrides_env(self, audio_service):
        """backend='assemblyai' overrides LLM_BACKEND=qwen env var."""
        with patch.object(audio_service, 'format_text_with_assemblyai', return_value='aai') as mock_a:
            result = audio_service.format_text_with_llm(LONG_TEXT, backend='assemblyai')
            mock_a.assert_called_once()
            assert result == 'aai'

    @patch.dict(os.environ, {'LLM_BACKEND': 'assemblyai'})
    def test_backend_param_none_uses_env(self, audio_service):
        """backend=None → falls back to LLM_BACKEND env var."""
        with patch.object(audio_service, 'format_text_with_assemblyai', return_value='aai') as mock_a:
            result = audio_service.format_text_with_llm(LONG_TEXT, backend=None)
            mock_a.assert_called_once()
            assert result == 'aai'


# ============== Fallback Chain Tests ==============

class TestFallbackChains:
    """Test fallback chains between LLM backends."""

    @patch('requests.post')
    def test_qwen_ok_no_fallback(self, mock_post, audio_service):
        """Qwen succeeds → no fallback called."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'output': {'text': 'Formatted by Qwen.'}
        }
        mock_post.return_value = mock_resp

        with patch.object(audio_service, 'format_text_with_assemblyai') as mock_aai:
            result = audio_service.format_text_with_qwen(LONG_TEXT)
            mock_aai.assert_not_called()
            assert result == 'Formatted by Qwen.'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_qwen_fail_assemblyai_ok(self, mock_post):
        """Qwen fails → AssemblyAI succeeds (default chain)."""
        service = AudioService(alibaba_api_key='test-key')

        # First call (Qwen) fails, second call (AAI) succeeds
        qwen_resp = MagicMock()
        qwen_resp.status_code = 500
        qwen_resp.text = 'Internal error'

        aai_resp = MagicMock()
        aai_resp.status_code = 200
        aai_resp.json.return_value = {
            'choices': [{'message': {'content': 'Formatted by AAI.'}}]
        }

        mock_post.side_effect = [qwen_resp, aai_resp]

        result = service.format_text_with_qwen(LONG_TEXT)
        assert result == 'Formatted by AAI.'

    @patch('requests.post')
    @patch.dict(os.environ, {'ASSEMBLYAI_API_KEY': 'fake-key'})
    def test_assemblyai_fail_qwen_ok(self, mock_post):
        """AssemblyAI fails → Qwen succeeds (assemblyai chain)."""
        service = AudioService(alibaba_api_key='test-key')

        # First call (AAI) fails, second call (Qwen) succeeds
        aai_resp = MagicMock()
        aai_resp.status_code = 500
        aai_resp.text = 'Internal error'

        qwen_resp = MagicMock()
        qwen_resp.status_code = 200
        qwen_resp.json.return_value = {
            'output': {'text': 'Formatted by Qwen.'}
        }

        mock_post.side_effect = [aai_resp, qwen_resp]

        result = service.format_text_with_assemblyai(LONG_TEXT)
        assert result == 'Formatted by Qwen.'
