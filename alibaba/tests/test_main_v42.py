#!/usr/bin/env python3
"""
Unit tests for webhook-handler/main.py — auto-trial, /start, revoke, balance check.
v4.2.0 — auto-trial at registration, remove /trial and /review_trials.
Run with: python -m pytest alibaba/tests/test_main_v42.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

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
    return db


@pytest.fixture
def mock_tg():
    tg = MagicMock()
    tg.send_message.return_value = {'ok': True, 'result': {'message_id': 1}}
    tg.edit_message_text.return_value = {'ok': True}
    tg.answer_callback_query.return_value = {'ok': True}
    return tg


@pytest.fixture
def _patch_services(mock_db, mock_tg):
    with patch('main.get_db_service', return_value=mock_db), \
         patch('main.get_telegram_service', return_value=mock_tg), \
         patch('main.OWNER_ID', 999):
        yield


def make_message(user_id=12345, text='/start', first_name='Иван', username='ivan'):
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


# ==================== Auto-trial at registration ====================

class TestAutoTrial:
    """New user gets TRIAL_MINUTES automatically."""

    def test_new_user_created_with_trial_balance(self, mock_db, mock_tg, _patch_services):
        """New user gets balance_minutes=15, trial_status='approved'."""
        import main

        # First call returns None (new user), second returns created user
        mock_db.get_user.side_effect = [
            None,
            {'user_id': '12345', 'balance_minutes': 15, 'trial_status': 'approved'},
        ]

        result = main.handle_message(make_message())

        # Verify create_user called with auto-trial values
        mock_db.create_user.assert_called_once()
        call_args = mock_db.create_user.call_args
        user_data = call_args[0][1]
        assert user_data['balance_minutes'] == main.TRIAL_MINUTES
        assert user_data['trial_status'] == 'approved'

    def test_admin_notified_about_new_user(self, mock_db, mock_tg, _patch_services):
        """Admin receives notification with revoke button for new user."""
        import main

        mock_db.get_user.side_effect = [
            None,
            {'user_id': '12345', 'balance_minutes': 15, 'trial_status': 'approved'},
        ]

        main.handle_message(make_message(user_id=12345, first_name='Иван', username='ivan'))

        # Find the admin notification call (to OWNER_ID=999)
        admin_calls = [
            c for c in mock_tg.send_message.call_args_list
            if c[0][0] == 999
        ]
        assert len(admin_calls) == 1
        admin_msg = admin_calls[0]
        assert '@ivan' in admin_msg[0][1]
        assert '12345' in admin_msg[0][1]
        assert admin_msg[1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'revoke_trial_12345'

    def test_admin_notification_uses_first_name_without_username(self, mock_db, mock_tg, _patch_services):
        """Admin notification shows first_name when username is missing."""
        import main

        mock_db.get_user.side_effect = [
            None,
            {'user_id': '12345', 'balance_minutes': 15, 'trial_status': 'approved'},
        ]

        main.handle_message(make_message(user_id=12345, first_name='Анна', username=''))

        admin_calls = [
            c for c in mock_tg.send_message.call_args_list
            if c[0][0] == 999
        ]
        assert len(admin_calls) == 1
        assert 'Анна' in admin_calls[0][0][1]

    def test_existing_user_not_recreated(self, mock_db, mock_tg, _patch_services):
        """Existing user is not recreated on subsequent messages."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 10, 'trial_status': 'approved',
        }

        main.handle_message(make_message())

        mock_db.create_user.assert_not_called()


# ==================== /start command ====================

class TestStartCommand:
    """Test /start greeting with value proposition."""

    def test_start_shows_trial_balance(self, mock_db, mock_tg, _patch_services):
        """New user with trial sees gift message."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 15, 'trial_status': 'approved',
        }

        result = main.handle_message(make_message(text='/start'))

        assert result == 'start'
        msg = mock_tg.send_message.call_args[0][1]
        assert '15 бесплатных минут' in msg
        assert 'перешлите голосовое' in msg.lower()
        assert 'parse_mode' in mock_tg.send_message.call_args[1]

    def test_start_shows_paid_balance(self, mock_db, mock_tg, _patch_services):
        """Paid user sees balance without gift emoji."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 50, 'trial_status': 'none',
        }

        result = main.handle_message(make_message(text='/start'))

        assert result == 'start'
        msg = mock_tg.send_message.call_args[0][1]
        assert '50 мин' in msg
        assert '🎁' not in msg
        assert 'перешлите голосовое' in msg.lower()

    def test_start_shows_buy_when_zero_balance(self, mock_db, mock_tg, _patch_services):
        """User with 0 balance sees buy prompt."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 0, 'trial_status': 'denied',
        }

        result = main.handle_message(make_message(text='/start'))

        assert result == 'start'
        msg = mock_tg.send_message.call_args[0][1]
        assert '/buy_minutes' in msg

    def test_start_contains_features(self, mock_db, mock_tg, _patch_services):
        """Start message contains feature list."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 15, 'trial_status': 'approved',
        }

        main.handle_message(make_message(text='/start'))

        msg = mock_tg.send_message.call_args[0][1]
        assert 'спикер' in msg.lower()
        assert 'форматирование' in msg.lower()


# ==================== Revoke trial callback ====================

class TestRevokeTrialCallback:
    """Test revoke_trial_ callback handler."""

    def test_revoke_trial_sets_balance_zero(self, mock_db, mock_tg, _patch_services):
        """Revoke trial sets balance=0 and trial_status=denied."""
        import main

        callback = {
            'id': 'cb1',
            'from': {'id': 999},  # OWNER_ID
            'message': {'chat': {'id': 999}, 'message_id': 42},
            'data': 'revoke_trial_12345',
        }

        result = main.handle_callback_query(callback)

        assert result == 'trial_revoked'
        mock_db.update_user.assert_called_once_with(12345, {
            'trial_status': 'denied', 'balance_minutes': 0,
        })
        mock_tg.edit_message_text.assert_called_once()
        edit_msg = mock_tg.edit_message_text.call_args[0][2]
        assert '12345' in edit_msg
        assert 'отозван' in edit_msg.lower()

    def test_revoke_trial_only_for_owner(self, mock_db, mock_tg, _patch_services):
        """Non-owner cannot use revoke_trial callback."""
        import main

        callback = {
            'id': 'cb1',
            'from': {'id': 777},  # Not OWNER_ID
            'message': {'chat': {'id': 777}, 'message_id': 42},
            'data': 'revoke_trial_12345',
        }

        result = main.handle_callback_query(callback)

        assert result == 'unauthorized_callback'
        mock_db.update_user.assert_not_called()


# ==================== Balance check ====================

class TestBalanceCheck:
    """Balance check always blocks when insufficient — no trial bypass."""

    def test_insufficient_balance_blocks_always(self, mock_db, mock_tg, _patch_services):
        """Audio is rejected when balance < duration, regardless of trial_status."""
        import main

        for trial_status in ['none', 'approved', 'pending', 'denied']:
            mock_tg.reset_mock()
            user = {
                'user_id': '12345',
                'balance_minutes': 0,
                'trial_status': trial_status,
            }

            result = main.handle_audio_message(
                {
                    'chat': {'id': 12345},
                    'from': {'id': 12345},
                    'voice': {'file_id': 'abc', 'duration': 60},
                },
                user,
            )

            assert result == 'insufficient_balance', \
                f"Expected insufficient_balance for trial_status={trial_status}"

    def test_insufficient_balance_message_has_buy_minutes(self, mock_db, mock_tg, _patch_services):
        """Insufficient balance message mentions /buy_minutes."""
        import main

        user = {
            'user_id': '12345',
            'balance_minutes': 0,
            'trial_status': 'approved',
        }

        main.handle_audio_message(
            {
                'chat': {'id': 12345},
                'from': {'id': 12345},
                'voice': {'file_id': 'abc', 'duration': 60},
            },
            user,
        )

        msg = mock_tg.send_message.call_args[0][1]
        assert '/buy_minutes' in msg
        assert '/trial' not in msg


# ==================== /trial removed ====================

class TestTrialRemoved:
    """/trial command should return unknown_command."""

    def test_trial_command_unknown(self, mock_db, mock_tg, _patch_services):
        """/trial returns unknown_command."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 0, 'trial_status': 'none',
        }

        result = main.handle_message(make_message(text='/trial'))

        assert result == 'unknown_command'

    def test_help_does_not_mention_trial(self, mock_db, mock_tg, _patch_services):
        """/help output does not contain /trial."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '12345', 'balance_minutes': 0, 'trial_status': 'none',
        }

        main.handle_message(make_message(text='/help'))

        msg = mock_tg.send_message.call_args[0][1]
        assert '/trial' not in msg


# ==================== /review_trials removed ====================

class TestReviewTrialsRemoved:
    """/review_trials should return unknown_command."""

    def test_review_trials_unknown(self, mock_db, mock_tg, _patch_services):
        """/review_trials returns unknown_command for admin."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '999', 'balance_minutes': 0, 'trial_status': 'none',
        }

        result = main.handle_message(make_message(user_id=999, text='/review_trials'))

        assert result == 'unknown_command'

    def test_admin_help_no_review_trials(self, mock_db, mock_tg, _patch_services):
        """/admin help does not mention /review_trials."""
        import main

        mock_db.get_user.return_value = {
            'user_id': '999', 'balance_minutes': 0, 'trial_status': 'none',
        }

        main.handle_message(make_message(user_id=999, text='/admin'))

        msg = mock_tg.send_message.call_args[0][1]
        assert 'review_trials' not in msg
