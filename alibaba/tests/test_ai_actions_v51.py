#!/usr/bin/env python3
"""
Unit tests for v5.1.0 AI Action buttons feature:
- _handle_ai_action callback handling
- Beta counter (get/increment)
- _call_gemini_pro API calls
- _send_ai_action_buttons (in handler.py)
- Integration with handle_callback_query

Run with: python -m pytest alibaba/tests/test_ai_actions_v51.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio-processor'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shared'))

import pytest


# === Fixtures ===

@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token-123')
    monkeypatch.setenv('OWNER_ID', '999')
    monkeypatch.setenv('TABLESTORE_ENDPOINT', 'https://test.ots.aliyuncs.com')
    monkeypatch.setenv('TABLESTORE_INSTANCE', 'test')
    monkeypatch.setenv('ALIBABA_ACCESS_KEY', 'test-ak')
    monkeypatch.setenv('ALIBABA_SECRET_KEY', 'test-sk')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('ASSEMBLYAI_API_KEY', 'test-aai-key')
    monkeypatch.setenv('OSS_ENDPOINT', 'oss-eu-central-1.aliyuncs.com')
    monkeypatch.setenv('OSS_BUCKET', 'test-bucket')


@pytest.fixture
def mock_tg():
    tg = MagicMock()
    tg.send_message.return_value = {'result': {'message_id': 100}}
    tg.edit_message_text.return_value = True
    tg.delete_message.return_value = True
    tg.send_chat_action.return_value = True
    tg.edit_message_reply_markup.return_value = True
    tg.send_long_message.return_value = True
    tg.send_as_file.return_value = True
    return tg


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_user.return_value = {'ai_beta_count': 5}
    db.update_user.return_value = True
    db.get_job.return_value = {
        'job_id': 'test-job-123',
        'status': 'completed',
        'transcript': 'Test transcript with enough content for AI processing.',
    }
    return db


# === _get_ai_beta_count ===

class TestGetAiBetaCount:
    def test_returns_count_from_db(self, mock_db):
        from main import _get_ai_beta_count
        mock_db.get_user.return_value = {'ai_beta_count': 42}
        assert _get_ai_beta_count(mock_db) == 42
        mock_db.get_user.assert_called_once_with(0)

    def test_returns_zero_when_no_row(self, mock_db):
        from main import _get_ai_beta_count
        mock_db.get_user.return_value = None
        assert _get_ai_beta_count(mock_db) == 0

    def test_returns_zero_on_exception(self, mock_db):
        from main import _get_ai_beta_count
        mock_db.get_user.side_effect = Exception("DB error")
        assert _get_ai_beta_count(mock_db) == 0

    def test_returns_zero_when_field_missing(self, mock_db):
        from main import _get_ai_beta_count
        mock_db.get_user.return_value = {'some_other_field': 'value'}
        assert _get_ai_beta_count(mock_db) == 0


# === _increment_ai_beta_count ===

class TestIncrementAiBetaCount:
    def test_increments_existing_count(self, mock_db):
        from main import _increment_ai_beta_count
        mock_db.get_user.return_value = {'ai_beta_count': 10}
        mock_db.update_user.return_value = True
        result = _increment_ai_beta_count(mock_db)
        assert result == 11
        mock_db.update_user.assert_called_once_with(0, {'ai_beta_count': 11})

    def test_creates_row_when_update_fails(self, mock_db):
        from main import _increment_ai_beta_count
        mock_db.get_user.return_value = None
        mock_db.update_user.return_value = False
        result = _increment_ai_beta_count(mock_db)
        assert result == 1
        mock_db.create_user.assert_called_once_with(0, {'ai_beta_count': 1})

    def test_returns_negative_on_update_error(self, mock_db):
        from main import _increment_ai_beta_count
        mock_db.get_user.return_value = {'ai_beta_count': 5}
        mock_db.update_user.side_effect = Exception("DB error")
        result = _increment_ai_beta_count(mock_db)
        assert result == -1


# === _call_gemini_pro ===

class TestCallGeminiPro:
    def test_returns_none_when_no_api_key(self, monkeypatch):
        from main import _call_gemini_pro
        monkeypatch.delenv('ASSEMBLYAI_API_KEY', raising=False)
        result = _call_gemini_pro('system', 'user prompt')
        assert result is None

    def test_successful_call(self):
        import requests as req_lib
        from main import _call_gemini_pro
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': 'Generated text here'}}]
        }
        with patch.object(req_lib, 'post', return_value=mock_response) as mock_post:
            result = _call_gemini_pro('system prompt', 'user prompt')

        assert result == 'Generated text here'
        call_args = mock_post.call_args
        assert 'llm-gateway.assemblyai.com' in call_args[0][0]
        payload = call_args[1]['json']
        assert payload['model'] == 'gemini-3.1-pro-preview'
        assert payload['max_tokens'] == 16384

    def test_returns_none_on_http_error(self):
        import requests as req_lib
        from main import _call_gemini_pro
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        with patch.object(req_lib, 'post', return_value=mock_response):
            result = _call_gemini_pro('system', 'user')
        assert result is None

    def test_returns_none_on_empty_content(self):
        import requests as req_lib
        from main import _call_gemini_pro
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': ''}}]
        }
        with patch.object(req_lib, 'post', return_value=mock_response):
            result = _call_gemini_pro('system', 'user')
        assert result is None

    def test_returns_none_on_timeout(self):
        import requests as req_lib
        from main import _call_gemini_pro
        with patch.object(req_lib, 'post', side_effect=req_lib.exceptions.Timeout("timeout")):
            result = _call_gemini_pro('system', 'user')
        assert result is None


# === _handle_ai_action ===

class TestHandleAiAction:
    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_successful_news_action(self, mock_get_count, mock_inc_count, mock_gemini,
                                     mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 5
        mock_inc_count.return_value = 6
        mock_gemini.return_value = 'Generated news article text'

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)

        assert result == 'ai_action_news_done'
        mock_db.get_job.assert_called_once_with('job123')
        mock_gemini.assert_called_once()
        # Verify progress message was sent
        mock_tg.send_message.assert_called()
        # Verify buttons were removed
        mock_tg.edit_message_reply_markup.assert_called_once_with(456, 789, reply_markup='')

    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_successful_summary_action(self, mock_get_count, mock_inc_count, mock_gemini,
                                        mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 10
        mock_inc_count.return_value = 11
        mock_gemini.return_value = 'Summary content here'

        result = _handle_ai_action('ai_sum_job456', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_sum_done'

    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_successful_tasks_action(self, mock_get_count, mock_inc_count, mock_gemini,
                                      mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 20
        mock_inc_count.return_value = 21
        mock_gemini.return_value = '1. Task one\n2. Task two'

        result = _handle_ai_action('ai_task_job789', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_task_done'

    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_successful_screenplay_action(self, mock_get_count, mock_inc_count, mock_gemini,
                                           mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 30
        mock_inc_count.return_value = 31
        mock_gemini.return_value = 'FADE IN:\nINT. OFFICE - DAY'

        result = _handle_ai_action('ai_scr_jobabc', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_scr_done'

    @patch('main._get_ai_beta_count')
    def test_beta_limit_reached(self, mock_get_count, mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 100

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_beta_limit_reached'
        mock_tg.send_message.assert_called_once()
        assert '100/100' in mock_tg.send_message.call_args[0][1]

    def test_malformed_callback(self, mock_tg, mock_db):
        from main import _handle_ai_action
        result = _handle_ai_action('ai_bad', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_malformed'

    @patch('main._get_ai_beta_count')
    def test_unknown_action(self, mock_get_count, mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        result = _handle_ai_action('ai_xyz_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_unknown'

    @patch('main._get_ai_beta_count')
    def test_job_not_found(self, mock_get_count, mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        mock_db.get_job.return_value = None

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_job_not_found'

    @patch('main._get_ai_beta_count')
    def test_transcript_missing(self, mock_get_count, mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        mock_db.get_job.return_value = {'status': 'completed', 'transcript': ''}

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_transcript_missing'

    @patch('main._call_gemini_pro')
    @patch('main._get_ai_beta_count')
    def test_generation_failed(self, mock_get_count, mock_gemini, mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        mock_gemini.return_value = None

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_generation_failed'
        # Progress message should be updated with error
        mock_tg.edit_message_text.assert_called()

    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_long_result_sent_as_file(self, mock_get_count, mock_inc_count, mock_gemini,
                                       mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        mock_inc_count.return_value = 1
        mock_gemini.return_value = 'A' * 5000  # Over 4000 char threshold

        result = _handle_ai_action('ai_news_job123', 123, 456, 789, mock_tg, mock_db)
        assert result == 'ai_action_news_done'
        mock_tg.send_as_file.assert_called_once()
        call_args = mock_tg.send_as_file.call_args
        assert 'news_' in call_args[1]['filename'] or 'news_' in call_args[0][2] if len(call_args[0]) > 2 else 'news_' in str(call_args)

    @patch('main._call_gemini_pro')
    @patch('main._increment_ai_beta_count')
    @patch('main._get_ai_beta_count')
    def test_transcript_passed_to_prompt(self, mock_get_count, mock_inc_count, mock_gemini,
                                          mock_tg, mock_db):
        from main import _handle_ai_action
        mock_get_count.return_value = 0
        mock_inc_count.return_value = 1
        mock_gemini.return_value = 'Result'
        mock_db.get_job.return_value = {
            'status': 'completed',
            'transcript': 'Specific transcript content here',
        }

        _handle_ai_action('ai_sum_job123', 123, 456, 789, mock_tg, mock_db)

        # Verify transcript was embedded in prompt
        gemini_call = mock_gemini.call_args
        user_prompt = gemini_call[0][1]
        assert 'Specific transcript content here' in user_prompt


# === callback routing ===

class TestCallbackRouting:
    @patch('main.get_db_service')
    @patch('main.get_telegram_service')
    @patch('main._handle_ai_action')
    def test_ai_callback_routed_for_regular_user(self, mock_handler, mock_get_tg, mock_get_db):
        from main import handle_callback_query
        mock_tg = MagicMock()
        mock_db = MagicMock()
        mock_get_tg.return_value = mock_tg
        mock_get_db.return_value = mock_db
        mock_handler.return_value = 'ai_action_news_done'

        callback = {
            'id': 'cb123',
            'from': {'id': 555},  # Regular user, not OWNER_ID=999
            'message': {'chat': {'id': 555}, 'message_id': 100},
            'data': 'ai_news_job123',
        }

        with patch('main.OWNER_ID', 999):
            result = handle_callback_query(callback)

        assert result == 'ai_action_news_done'
        mock_handler.assert_called_once_with('ai_news_job123', 555, 555, 100, mock_tg, mock_db)

    @patch('main.get_db_service')
    @patch('main.get_telegram_service')
    @patch('main._handle_ai_action')
    def test_ai_callback_routed_for_owner(self, mock_handler, mock_get_tg, mock_get_db):
        from main import handle_callback_query
        mock_tg = MagicMock()
        mock_db = MagicMock()
        mock_get_tg.return_value = mock_tg
        mock_get_db.return_value = mock_db
        mock_handler.return_value = 'ai_action_sum_done'

        callback = {
            'id': 'cb456',
            'from': {'id': 999},  # OWNER_ID
            'message': {'chat': {'id': 999}, 'message_id': 200},
            'data': 'ai_sum_job456',
        }

        with patch('main.OWNER_ID', 999):
            result = handle_callback_query(callback)

        assert result == 'ai_action_sum_done'
        mock_handler.assert_called_once()


# === _send_ai_action_buttons (handler.py) ===

class TestSendAiActionButtons:
    def test_sends_four_buttons(self):
        from handler import _send_ai_action_buttons
        tg = MagicMock()
        tg.send_message.return_value = True

        _send_ai_action_buttons(tg, 123, 'job-abc')

        tg.send_message.assert_called_once()
        call_args = tg.send_message.call_args
        markup = json.loads(call_args[1]['reply_markup'])
        buttons = markup['inline_keyboard']
        assert len(buttons) == 2  # Two rows
        assert len(buttons[0]) == 2  # Two buttons per row
        assert len(buttons[1]) == 2

        # Verify callback data format
        all_callbacks = [b['callback_data'] for row in buttons for b in row]
        assert 'ai_news_job-abc' in all_callbacks
        assert 'ai_sum_job-abc' in all_callbacks
        assert 'ai_task_job-abc' in all_callbacks
        assert 'ai_scr_job-abc' in all_callbacks

    def test_handles_send_error_gracefully(self):
        from handler import _send_ai_action_buttons
        tg = MagicMock()
        tg.send_message.side_effect = Exception("Network error")

        # Should not raise
        _send_ai_action_buttons(tg, 123, 'job-abc')


# === AI_ACTION_PROMPTS validation ===

class TestAiActionPrompts:
    def test_all_actions_have_required_fields(self):
        from main import AI_ACTION_PROMPTS
        for action, config in AI_ACTION_PROMPTS.items():
            assert 'title' in config, f"Missing title for {action}"
            assert 'system' in config, f"Missing system for {action}"
            assert 'prompt' in config, f"Missing prompt for {action}"
            assert '{transcript}' in config['prompt'], f"Missing {{transcript}} in prompt for {action}"

    def test_all_expected_actions_present(self):
        from main import AI_ACTION_PROMPTS
        expected = {'news', 'sum', 'task', 'scr'}
        assert set(AI_ACTION_PROMPTS.keys()) == expected

    def test_callback_data_length_within_telegram_limit(self):
        """Telegram callback_data max is 64 bytes. job_id is UUID (36 chars)."""
        from main import AI_ACTION_PROMPTS
        max_job_id = 'a' * 36  # UUID length
        for action in AI_ACTION_PROMPTS:
            callback = f'ai_{action}_{max_job_id}'
            assert len(callback.encode('utf-8')) <= 64, f"Callback too long for {action}: {len(callback.encode('utf-8'))} bytes"
