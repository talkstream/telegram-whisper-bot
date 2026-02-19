#!/usr/bin/env python3
"""
Unit tests for admin commands in webhook-handler/main.py.
Covers all /admin, /credit, /user, /stat, /cost, /status, /flush,
/export, /report, /metrics, /batch, /mute, /debug, /llm commands
and unauthorized access checks.
Run with: python -m pytest alibaba/tests/test_admin_commands.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'webhook-handler'))

import pytest


# Patch env vars before importing main
@pytest.fixture(autouse=True)
def _env_setup(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('OWNER_ID', '999')
    monkeypatch.setenv('TABLESTORE_ENDPOINT', 'https://test.ots.aliyuncs.com')
    monkeypatch.setenv('TABLESTORE_INSTANCE', 'test')
    monkeypatch.setenv('ALIBABA_ACCESS_KEY', 'test-ak')
    monkeypatch.setenv('ALIBABA_SECRET_KEY', 'test-sk')


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_user.return_value = None
    db.create_user.return_value = True
    db.update_user.return_value = True
    db.update_user_balance.return_value = True
    db.get_user_settings.return_value = {}
    db.update_user_settings.return_value = True
    db.search_users.return_value = []
    db.get_all_users.return_value = []
    db.get_transcription_stats.return_value = {
        'total_count': 0, 'total_seconds': 0, 'total_chars': 0, 'user_stats': {},
    }
    db.get_payment_stats.return_value = {
        'total_count': 0, 'total_stars': 0, 'total_minutes': 0,
    }
    db.count_pending_jobs.return_value = 0
    db.get_pending_jobs.return_value = []
    db.get_stuck_jobs.return_value = []
    db.delete_job.return_value = True
    return db


@pytest.fixture
def mock_tg():
    tg = MagicMock()
    tg.send_message.return_value = {'ok': True, 'result': {'message_id': 1}}
    tg.edit_message_text.return_value = {'ok': True}
    tg.answer_callback_query.return_value = {'ok': True}
    tg.send_document.return_value = {'ok': True}
    tg.delete_message.return_value = {'ok': True}
    return tg


@pytest.fixture
def _patch_services(mock_db, mock_tg):
    with patch('main.get_db_service', return_value=mock_db), \
         patch('main.get_telegram_service', return_value=mock_tg), \
         patch('main.OWNER_ID', 999):
        yield


def make_message(user_id=999, text='/admin', first_name='Admin', username='admin'):
    """Build a Telegram message dict. Default user_id=999 (OWNER_ID)."""
    return {
        'message_id': 1,
        'from': {
            'id': user_id,
            'is_bot': False,
            'first_name': first_name,
            'username': username,
        },
        'chat': {'id': user_id, 'type': 'private'},
        'date': 1700000000,
        'text': text,
    }


def make_admin_user():
    """Return a user dict for the admin/owner."""
    return {
        'user_id': '999',
        'balance_minutes': 100,
        'trial_status': 'none',
        'settings': '{}',
    }


# ==================== Unauthorized access ====================

class TestUnauthorizedAccess:
    """Non-owner users must not reach admin command handlers."""

    @pytest.mark.parametrize('command', [
        '/admin', '/credit 123 10', '/user', '/stat', '/cost',
        '/status', '/flush', '/export', '/report', '/metrics',
        '/batch 123', '/mute', '/debug', '/llm',
    ])
    def test_admin_commands_rejected_for_regular_user(self, command, mock_db, mock_tg, _patch_services):
        """Every admin command returns 'unauthorized' for non-owner."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 10, 'trial_status': 'none',
        }

        result = main.handle_message(make_message(user_id=12345, text=command))

        assert result == 'unauthorized'


# ==================== /admin ====================

class TestAdminHelp:
    """Test /admin command shows admin help text."""

    def test_admin_help_returns_admin_help(self, mock_db, mock_tg, _patch_services):
        """/admin dispatches through handle_command and returns admin_help."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/admin'))

        assert result == 'admin_help'

    def test_admin_help_contains_all_commands(self, mock_db, mock_tg, _patch_services):
        """Admin help message lists all admin commands."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        main.handle_message(make_message(text='/admin'))

        msg = mock_tg.send_message.call_args[0][1]
        for cmd in ['/user', '/credit', '/stat', '/cost', '/metrics',
                    '/status', '/flush', '/batch', '/mute', '/debug', '/llm',
                    '/export', '/report']:
            assert cmd in msg, f"{cmd} not found in /admin help"


# ==================== /credit ====================

class TestCreditCommand:
    """Test /credit <id> <minutes> command."""

    def test_credit_adds_minutes(self, mock_db, mock_tg, _patch_services):
        """/credit 12345 30 adds 30 minutes to user 12345."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/credit 12345 30'))

        assert result == 'credit_added'
        mock_db.update_user_balance.assert_called_once_with(12345, 30.0)

    def test_credit_notifies_target_user(self, mock_db, mock_tg, _patch_services):
        """Target user receives notification about credited minutes."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        main.handle_message(make_message(text='/credit 12345 50'))

        # Two send_message calls: one to admin, one to target user
        target_calls = [
            c for c in mock_tg.send_message.call_args_list
            if c[0][0] == 12345
        ]
        assert len(target_calls) == 1
        assert '50' in target_calls[0][0][1]

    def test_credit_shows_usage_without_args(self, mock_db, mock_tg, _patch_services):
        """/credit without arguments returns credit_usage."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/credit'))

        assert result == 'credit_usage'

    def test_credit_handles_invalid_user_id(self, mock_db, mock_tg, _patch_services):
        """/credit with non-numeric user_id returns credit_error."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/credit abc 10'))

        assert result == 'credit_error'

    def test_credit_trial_minutes_special_message(self, mock_db, mock_tg, _patch_services):
        """Crediting exactly TRIAL_MINUTES sends 'trial approved' message to user."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        main.handle_message(make_message(text=f'/credit 12345 {main.TRIAL_MINUTES}'))

        target_calls = [
            c for c in mock_tg.send_message.call_args_list
            if c[0][0] == 12345
        ]
        assert len(target_calls) == 1
        assert 'пробный' in target_calls[0][0][1].lower() or 'заявк' in target_calls[0][0][1].lower()


# ==================== /user ====================

class TestUserSearch:
    """Test /user [search] command."""

    def test_user_list_all(self, mock_db, mock_tg, _patch_services):
        """/user without search lists all users."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_all_users.return_value = [
            {'user_id': '111', 'first_name': 'Alice', 'last_name': '', 'balance_minutes': 10, 'trial_status': 'approved'},
            {'user_id': '222', 'first_name': 'Bob', 'last_name': '', 'balance_minutes': 0, 'trial_status': 'denied'},
        ]

        result = main.handle_message(make_message(text='/user'))

        assert result == 'users_listed'
        msg = mock_tg.send_message.call_args[0][1]
        assert 'Alice' in msg
        assert 'Bob' in msg

    def test_user_search_by_query(self, mock_db, mock_tg, _patch_services):
        """/user alice searches by query."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.search_users.return_value = [
            {'user_id': '111', 'first_name': 'Alice', 'last_name': '', 'balance_minutes': 10, 'trial_status': 'approved'},
        ]

        result = main.handle_message(make_message(text='/user alice'))

        assert result == 'users_listed'
        mock_db.search_users.assert_called_once_with('alice')

    def test_user_search_not_found(self, mock_db, mock_tg, _patch_services):
        """/user nonexistent returns user_not_found."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.search_users.return_value = []

        result = main.handle_message(make_message(text='/user nonexistent'))

        assert result == 'user_not_found'


# ==================== /stat ====================

class TestStatCommand:
    """Test /stat command - usage statistics."""

    def test_stat_shows_statistics(self, mock_db, mock_tg, _patch_services):
        """/stat returns stat_shown with correct data."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_transcription_stats.return_value = {
            'total_count': 42,
            'total_seconds': 3600,
            'total_chars': 50000,
            'user_stats': {'111': {'duration': 1800}},
        }
        mock_db.get_all_users.return_value = [
            {'user_id': '111'}, {'user_id': '222'}, {'user_id': '333'},
        ]

        result = main.handle_message(make_message(text='/stat'))

        assert result == 'stat_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert '42' in msg  # total_count
        assert '3' in msg   # total_users


# ==================== /cost ====================

class TestCostCommand:
    """Test /cost command - cost analysis."""

    def test_cost_no_data(self, mock_db, mock_tg, _patch_services):
        """/cost with zero transcriptions returns no_cost_data."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_transcription_stats.return_value = {
            'total_count': 0, 'total_seconds': 0, 'total_chars': 0, 'user_stats': {},
        }

        result = main.handle_message(make_message(text='/cost'))

        assert result == 'no_cost_data'

    def test_cost_shows_breakdown(self, mock_db, mock_tg, _patch_services):
        """/cost with data shows cost breakdown."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_transcription_stats.return_value = {
            'total_count': 10,
            'total_seconds': 600,
            'total_chars': 10000,
            'user_stats': {},
        }

        result = main.handle_message(make_message(text='/cost'))

        assert result == 'cost_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert 'Qwen3-ASR' in msg
        assert 'Qwen-turbo' in msg
        assert 'Function Compute' in msg


# ==================== /status ====================

class TestStatusCommand:
    """Test /status command - queue status."""

    def test_status_empty_queue(self, mock_db, mock_tg, _patch_services):
        """/status with empty queue shows 'queue empty'."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.count_pending_jobs.return_value = 0

        result = main.handle_message(make_message(text='/status'))

        assert result == 'status_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert 'пуста' in msg.lower()

    def test_status_with_pending_jobs(self, mock_db, mock_tg, _patch_services):
        """/status with jobs shows job details."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.count_pending_jobs.return_value = 2
        mock_db.get_pending_jobs.return_value = [
            {'user_id': '111', 'status': 'pending', 'duration': 120},
            {'user_id': '222', 'status': 'processing', 'duration': 300},
        ]

        result = main.handle_message(make_message(text='/status'))

        assert result == 'status_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert '2' in msg
        assert '111' in msg
        assert '222' in msg


# ==================== /flush ====================

class TestFlushCommand:
    """Test /flush command - clean stuck jobs."""

    def test_flush_no_stuck_jobs(self, mock_db, mock_tg, _patch_services):
        """/flush with no stuck jobs returns no_stuck_jobs."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_stuck_jobs.return_value = []

        result = main.handle_message(make_message(text='/flush'))

        assert result == 'no_stuck_jobs'

    def test_flush_cleans_stuck_jobs_and_refunds(self, mock_db, mock_tg, _patch_services):
        """/flush deletes stuck jobs and refunds users."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_stuck_jobs.return_value = [
            {'job_id': 'job1', 'user_id': '111', 'duration': 120},
            {'job_id': 'job2', 'user_id': '111', 'duration': 60},
        ]

        result = main.handle_message(make_message(text='/flush'))

        assert result == 'flush_done'
        # Both jobs should be deleted
        assert mock_db.delete_job.call_count == 2
        # User 111 should get refund (120+60=180 seconds = 3 minutes)
        mock_db.update_user_balance.assert_called_once_with(111, 3.0)


# ==================== /export ====================

class TestExportCommand:
    """Test /export command - CSV exports."""

    def test_export_users(self, mock_db, mock_tg, _patch_services):
        """/export users sends CSV document."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_all_users.return_value = [
            {'user_id': '111', 'first_name': 'Alice', 'username': 'alice',
             'balance_minutes': 10, 'trial_status': 'approved', 'created_at': '2025-01-01'},
        ]

        result = main.handle_message(make_message(text='/export users'))

        assert result == 'export_done'
        mock_tg.send_document.assert_called_once()

    def test_export_invalid_type(self, mock_db, mock_tg, _patch_services):
        """/export with invalid type returns export_usage."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/export invalid'))

        assert result == 'export_usage'

    def test_export_logs_with_days(self, mock_db, mock_tg, _patch_services):
        """/export logs 7 passes days=7 and sends CSV."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/export logs 7'))

        assert result == 'export_done'
        mock_db.get_transcription_stats.assert_called_with(days=7)


# ==================== /report ====================

class TestReportCommand:
    """Test /report command."""

    def test_report_daily(self, mock_db, mock_tg, _patch_services):
        """/report daily shows daily report."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/report daily'))

        assert result == 'report_shown'
        mock_db.get_transcription_stats.assert_called_with(days=1)

    def test_report_weekly(self, mock_db, mock_tg, _patch_services):
        """/report weekly shows weekly report."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/report weekly'))

        assert result == 'report_shown'
        mock_db.get_transcription_stats.assert_called_with(days=7)

    def test_report_invalid_type(self, mock_db, mock_tg, _patch_services):
        """/report with invalid type returns report_usage."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/report monthly'))

        assert result == 'report_usage'


# ==================== /metrics ====================

class TestMetricsCommand:
    """Test /metrics command."""

    def test_metrics_default_24h(self, mock_db, mock_tg, _patch_services):
        """/metrics without args shows 24-hour metrics."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/metrics'))

        assert result == 'metrics_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert '24' in msg

    def test_metrics_custom_hours(self, mock_db, mock_tg, _patch_services):
        """/metrics 6 shows 6-hour metrics."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/metrics 6'))

        assert result == 'metrics_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert '6' in msg


# ==================== /batch ====================

class TestBatchCommand:
    """Test /batch command - user queue."""

    def test_batch_without_user_id(self, mock_db, mock_tg, _patch_services):
        """/batch without user_id returns batch_usage."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/batch'))

        assert result == 'batch_usage'

    def test_batch_no_jobs(self, mock_db, mock_tg, _patch_services):
        """/batch 12345 with no jobs returns no_batch_jobs."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_pending_jobs.return_value = []

        result = main.handle_message(make_message(text='/batch 12345'))

        assert result == 'no_batch_jobs'

    def test_batch_shows_user_jobs(self, mock_db, mock_tg, _patch_services):
        """/batch filters jobs by user_id."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_pending_jobs.return_value = [
            {'job_id': 'aaaa-bbbb-cccc', 'user_id': '12345', 'status': 'pending', 'duration': 60},
            {'job_id': 'dddd-eeee-ffff', 'user_id': '99999', 'status': 'pending', 'duration': 30},
        ]

        result = main.handle_message(make_message(text='/batch 12345'))

        assert result == 'batch_shown'
        msg = mock_tg.send_message.call_args[0][1]
        assert '12345' in msg
        assert 'aaaa-bbb' in msg  # first 8 chars of job_id


# ==================== /mute ====================

class TestMuteCommand:
    """Test /mute command - error notifications control."""

    def test_mute_status(self, mock_db, mock_tg, _patch_services):
        """/mute without args shows current mute status."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        with patch('services.utility.TelegramErrorHandler') as mock_handler:
            mock_handler.is_muted.return_value = False
            result = main.handle_message(make_message(text='/mute'))

        assert result == 'mute_status'

    def test_mute_set_hours(self, mock_db, mock_tg, _patch_services):
        """/mute 8 mutes for 8 hours."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        with patch('services.utility.TelegramErrorHandler') as mock_handler:
            result = main.handle_message(make_message(text='/mute 8'))

        assert result == 'mute_set'
        mock_handler.set_mute.assert_called_once_with(8.0)

    def test_mute_off(self, mock_db, mock_tg, _patch_services):
        """/mute off clears mute."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        with patch('services.utility.TelegramErrorHandler') as mock_handler:
            result = main.handle_message(make_message(text='/mute off'))

        assert result == 'mute_off'
        mock_handler.clear_mute.assert_called_once()

    def test_mute_bad_format(self, mock_db, mock_tg, _patch_services):
        """/mute with invalid arg returns mute_bad_format."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        with patch('services.utility.TelegramErrorHandler'):
            result = main.handle_message(make_message(text='/mute abc'))

        assert result == 'mute_bad_format'


# ==================== /debug ====================

class TestDebugCommand:
    """Test /debug command - toggle diarization debug."""

    def test_debug_toggle_on(self, mock_db, mock_tg, _patch_services):
        """/debug enables debug when currently off."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'debug_mode': False}

        result = main.handle_message(make_message(text='/debug'))

        assert result == 'debug_toggle'
        updated_settings = mock_db.update_user_settings.call_args[0][1]
        assert updated_settings['debug_mode'] is True

    def test_debug_toggle_off(self, mock_db, mock_tg, _patch_services):
        """/debug disables debug when currently on."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'debug_mode': True}

        result = main.handle_message(make_message(text='/debug'))

        assert result == 'debug_toggle'
        updated_settings = mock_db.update_user_settings.call_args[0][1]
        assert updated_settings['debug_mode'] is False


# ==================== /llm ====================

class TestLlmCommand:
    """Test /llm command - LLM backend selection."""

    def test_llm_show_current(self, mock_db, mock_tg, _patch_services):
        """/llm without args shows current backend."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'llm_backend': 'qwen'}

        result = main.handle_message(make_message(text='/llm'))

        assert result == 'llm_show'
        msg = mock_tg.send_message.call_args[0][1]
        assert 'qwen' in msg

    def test_llm_set_assemblyai(self, mock_db, mock_tg, _patch_services):
        """/llm assemblyai switches to assemblyai backend."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'llm_backend': 'qwen'}

        result = main.handle_message(make_message(text='/llm assemblyai'))

        assert result == 'llm_set'
        updated_settings = mock_db.update_user_settings.call_args[0][1]
        assert updated_settings['llm_backend'] == 'assemblyai'

    def test_llm_set_qwen(self, mock_db, mock_tg, _patch_services):
        """/llm qwen switches to qwen backend."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'llm_backend': 'assemblyai'}

        result = main.handle_message(make_message(text='/llm qwen'))

        assert result == 'llm_set'
        updated_settings = mock_db.update_user_settings.call_args[0][1]
        assert updated_settings['llm_backend'] == 'qwen'

    def test_llm_invalid_backend(self, mock_db, mock_tg, _patch_services):
        """/llm with unknown backend shows current without changing."""
        import main

        mock_db.get_user.return_value = make_admin_user()
        mock_db.get_user_settings.return_value = {'llm_backend': 'qwen'}

        result = main.handle_message(make_message(text='/llm gemini'))

        assert result == 'llm_show'
        mock_db.update_user_settings.assert_not_called()


# ==================== Dispatch table coverage ====================

class TestDispatchTable:
    """Verify all admin commands are routed through the dispatch table."""

    def test_admin_commands_in_dispatch_table(self, _patch_services):
        """All documented admin commands exist in _ADMIN_COMMANDS."""
        import main

        expected_commands = [
            '/admin', '/credit', '/user', '/stat', '/cost',
            '/status', '/flush', '/export', '/report', '/metrics',
            '/batch', '/mute', '/debug', '/llm',
        ]
        for cmd in expected_commands:
            assert cmd in main._ADMIN_COMMANDS, f"{cmd} missing from _ADMIN_COMMANDS"

    def test_unknown_command_for_owner(self, mock_db, mock_tg, _patch_services):
        """Owner sending unknown command gets unknown_command response."""
        import main

        mock_db.get_user.return_value = make_admin_user()

        result = main.handle_message(make_message(text='/nonexistent'))

        assert result == 'unknown_command'
