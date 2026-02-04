"""
TablestoreService - Alibaba Cloud Tablestore adapter for Telegram Whisper Bot
Replaces FirestoreService for full Alibaba Cloud migration

Usage:
    from telegram_bot_shared.services.tablestore_service import TablestoreService

    service = TablestoreService(
        endpoint='https://instance.region.ots.aliyuncs.com',
        access_key_id='your-access-key-id',
        access_key_secret='your-access-key-secret',
        instance_name='your-instance-name'
    )
"""

import logging
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

import pytz

logger = logging.getLogger(__name__)


class TablestoreService:
    """
    Service for interacting with Alibaba Cloud Tablestore.
    API-compatible with FirestoreService for easy migration.
    """

    def __init__(self, endpoint: str, access_key_id: str,
                 access_key_secret: str, instance_name: str,
                 security_token: Optional[str] = None):
        """Initialize Tablestore client."""
        try:
            from tablestore import OTSClient
            self.client = OTSClient(
                endpoint,
                access_key_id,
                access_key_secret,
                instance_name,
                sts_token=security_token
            )
            self.instance_name = instance_name
            logger.info(f"TablestoreService initialized for instance: {instance_name}")
        except ImportError:
            logger.error("tablestore package not installed. Run: pip install tablestore")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize TablestoreService: {e}")
            raise

    # ==================== USER OPERATIONS ====================

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        from tablestore import Row, Condition, RowExistenceExpectation

        try:
            primary_key = [('user_id', str(user_id))]
            columns_to_get = []  # Empty = get all columns

            consumed, return_row, next_token = self.client.get_row(
                'users',
                primary_key,
                columns_to_get
            )

            if return_row is None:
                return None

            return self._row_to_dict(return_row)

        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    def create_user(self, user_id: int, user_data: Dict[str, Any]) -> bool:
        """Create a new user."""
        from tablestore import Row, Condition, RowExistenceExpectation

        try:
            primary_key = [('user_id', str(user_id))]

            # Prepare attribute columns
            attribute_columns = []
            for key, value in user_data.items():
                attribute_columns.append((key, self._serialize_value(value)))

            # Add created_at if not present
            if 'created_at' not in user_data:
                attribute_columns.append(('created_at', datetime.now(pytz.utc).isoformat()))

            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.EXPECT_NOT_EXIST)

            self.client.put_row('users', row, condition)
            logger.info(f"Created user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error creating user {user_id}: {e}")
            return False

    def update_user(self, user_id: int, updates: Dict[str, Any]) -> bool:
        """Update user data."""
        from tablestore import Row, Condition, RowExistenceExpectation

        try:
            primary_key = [('user_id', str(user_id))]

            # Prepare update columns - use lowercase 'put' as per SDK docs
            update_columns = {'put': []}
            for key, value in updates.items():
                update_columns['put'].append((key, self._serialize_value(value)))

            # Add last_activity timestamp
            update_columns['put'].append(('last_activity', datetime.now(pytz.utc).isoformat()))

            # Create Row object with primary key and update columns
            row = Row(primary_key, update_columns)
            condition = Condition(RowExistenceExpectation.EXPECT_EXIST)
            self.client.update_row('users', row, condition)
            logger.info(f"Updated user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return False

    def update_user_balance(self, user_id: int, delta: float, max_retries: int = 3) -> bool:
        """
        Update user balance with optimistic locking to prevent race conditions.
        Uses retry mechanism if concurrent update is detected.

        Args:
            user_id: The user ID
            delta: Amount to add (positive) or subtract (negative)
            max_retries: Maximum retry attempts on conflict

        Returns:
            True if balance was updated successfully, False otherwise
        """
        from tablestore import Row, Condition, RowExistenceExpectation, ComparatorType, SingleColumnCondition

        raw_balance = None  # Initialize for error logging

        for attempt in range(max_retries):
            try:
                # Get current balance
                user = self.get_user(user_id)
                if not user:
                    logger.error(f"User {user_id} not found for balance update")
                    return False

                # Get raw balance value for condition comparison (preserve original type)
                raw_balance = user.get('balance_minutes', 0)

                # Convert to float for arithmetic, but keep raw value for condition
                if isinstance(raw_balance, str):
                    current_balance = float(raw_balance)
                elif isinstance(raw_balance, (int, float)):
                    current_balance = float(raw_balance)
                else:
                    current_balance = 0.0
                    raw_balance = 0  # Default for condition

                # Keep balance as integer (Tablestore column type)
                new_balance = int(max(0, current_balance + delta))

                # Update with conditional check on current balance value
                primary_key = [('user_id', str(user_id))]
                # Use lowercase 'put' as per SDK docs
                update_columns = {
                    'put': [
                        ('balance_minutes', new_balance),  # Must be int for Tablestore
                        ('last_activity', datetime.now(pytz.utc).isoformat())
                    ]
                }

                # Create Row object with primary key and update columns
                row = Row(primary_key, update_columns)

                # Create condition: only update if balance_minutes equals current value
                # IMPORTANT: Use raw_balance to match the exact type stored in Tablestore
                condition = Condition(
                    RowExistenceExpectation.EXPECT_EXIST,
                    SingleColumnCondition(
                        'balance_minutes',
                        raw_balance,  # Use original value/type for comparison
                        ComparatorType.EQUAL,
                        pass_if_missing=True  # Pass if column doesn't exist (new user)
                    )
                )

                self.client.update_row('users', row, condition)
                logger.info(f"Updated balance for user {user_id}: {current_balance} -> {new_balance} (delta: {delta:+.0f})")
                return True

            except Exception as e:
                error_str = str(e)
                # Check if it's a condition failure (concurrent update or type mismatch)
                if 'OTSConditionCheckFail' in error_str or 'Condition check failed' in error_str:
                    logger.warning(f"Balance update conflict for user {user_id}, attempt {attempt + 1}/{max_retries}, raw_balance={raw_balance!r}")
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                        continue  # Retry
                    logger.error(f"Balance update failed after {max_retries} retries for user {user_id}")
                    return False
                else:
                    logger.error(f"Error updating balance for user {user_id}: {e}", exc_info=True)
                    return False

        return False

    def get_user_settings(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user settings."""
        user = self.get_user(user_id)
        if not user:
            return None

        settings_json = user.get('settings', '{}')
        try:
            return json.loads(settings_json) if isinstance(settings_json, str) else settings_json
        except json.JSONDecodeError:
            return {}

    def update_user_settings(self, user_id: int, settings: Dict[str, Any]) -> bool:
        """Update user settings."""
        return self.update_user(user_id, {'settings': json.dumps(settings)})

    # ==================== USER STATE OPERATIONS ====================

    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user state (for batch processing)."""
        from tablestore import Row

        try:
            primary_key = [('user_id', str(user_id))]
            consumed, return_row, next_token = self.client.get_row(
                'user_state',
                primary_key,
                []
            )

            if return_row is None:
                return None

            row_dict = self._row_to_dict(return_row)
            state_data = row_dict.get('state_data', '{}')
            return json.loads(state_data) if isinstance(state_data, str) else state_data

        except Exception as e:
            logger.error(f"Error getting user state {user_id}: {e}")
            return None

    def set_user_state(self, user_id: int, state: Optional[Dict[str, Any]]) -> bool:
        """Set user state."""
        from tablestore import Row, Condition, RowExistenceExpectation

        try:
            primary_key = [('user_id', str(user_id))]

            if state is None:
                # Delete state
                condition = Condition(RowExistenceExpectation.IGNORE)
                self.client.delete_row('user_state', primary_key, condition)
            else:
                # Set state
                attribute_columns = [('state_data', json.dumps(state))]
                row = Row(primary_key, attribute_columns)
                condition = Condition(RowExistenceExpectation.IGNORE)
                self.client.put_row('user_state', row, condition)

            return True

        except Exception as e:
            logger.error(f"Error setting user state {user_id}: {e}")
            return False

    # ==================== TRIAL REQUEST OPERATIONS ====================

    def create_trial_request(self, user_id: int, request_data: Dict[str, Any]) -> bool:
        """Create a trial request."""
        from tablestore import Row, Condition, RowExistenceExpectation

        try:
            primary_key = [('user_id', str(user_id))]
            attribute_columns = []

            for key, value in request_data.items():
                attribute_columns.append((key, self._serialize_value(value)))

            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.IGNORE)
            self.client.put_row('trial_requests', row, condition)

            logger.info(f"Created trial request for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error creating trial request for {user_id}: {e}")
            return False

    def get_pending_trial_requests(self) -> List[Dict[str, Any]]:
        """Get all pending trial requests."""
        # Note: This requires a range query or secondary index in Tablestore
        # For simplicity, we'll scan the table (not efficient for large datasets)
        logger.warning("get_pending_trial_requests: Consider adding secondary index for production")
        return []  # TODO: Implement with proper indexing

    # ==================== JOB OPERATIONS ====================

    def create_job(self, job_data: Dict[str, Any]) -> str:
        """Create an audio processing job."""
        from tablestore import Row, Condition, RowExistenceExpectation

        job_id = job_data.get('job_id', str(uuid.uuid4()))

        try:
            primary_key = [('job_id', job_id)]
            attribute_columns = []

            for key, value in job_data.items():
                if key != 'job_id':
                    attribute_columns.append((key, self._serialize_value(value)))

            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.EXPECT_NOT_EXIST)
            self.client.put_row('audio_jobs', row, condition)

            logger.info(f"Created job {job_id}")
            return job_id

        except Exception as e:
            logger.error(f"Error creating job: {e}")
            return ""

    def update_job(self, job_id: str, updates: Dict[str, Any]) -> bool:
        """Update a job."""
        from tablestore import Condition, RowExistenceExpectation

        try:
            primary_key = [('job_id', job_id)]
            update_columns = {'PUT': []}

            for key, value in updates.items():
                update_columns['PUT'].append((key, self._serialize_value(value)))

            condition = Condition(RowExistenceExpectation.EXPECT_EXIST)
            self.client.update_row('audio_jobs', primary_key, update_columns, condition)

            return True

        except Exception as e:
            logger.error(f"Error updating job {job_id}: {e}")
            return False

    # ==================== LOG OPERATIONS ====================

    def log_transcription(self, log_data: Dict[str, Any]) -> bool:
        """Log a transcription event."""
        from tablestore import Row, Condition, RowExistenceExpectation

        log_id = str(uuid.uuid4())

        try:
            primary_key = [('log_id', log_id)]
            attribute_columns = []

            for key, value in log_data.items():
                attribute_columns.append((key, self._serialize_value(value)))

            # Add timestamp if not present
            if 'timestamp' not in log_data:
                attribute_columns.append(('timestamp', datetime.now(pytz.utc).isoformat()))

            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.EXPECT_NOT_EXIST)
            self.client.put_row('transcription_logs', row, condition)

            return True

        except Exception as e:
            logger.error(f"Error logging transcription: {e}")
            return False

    def log_payment(self, payment_data: Dict[str, Any]) -> bool:
        """Log a payment event."""
        from tablestore import Row, Condition, RowExistenceExpectation

        payment_id = payment_data.get('payment_id', str(uuid.uuid4()))

        try:
            primary_key = [('payment_id', payment_id)]
            attribute_columns = []

            for key, value in payment_data.items():
                if key != 'payment_id':
                    attribute_columns.append((key, self._serialize_value(value)))

            if 'timestamp' not in payment_data:
                attribute_columns.append(('timestamp', datetime.now(pytz.utc).isoformat()))

            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.EXPECT_NOT_EXIST)
            self.client.put_row('payment_logs', row, condition)

            logger.info(f"Logged payment {payment_id}")
            return True

        except Exception as e:
            logger.error(f"Error logging payment: {e}")
            return False

    # ==================== HELPER METHODS ====================

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert Tablestore row to dictionary."""
        result = {}

        # Add primary key columns
        if row.primary_key:
            for pk in row.primary_key:
                result[pk[0]] = pk[1]

        # Add attribute columns
        if row.attribute_columns:
            for col in row.attribute_columns:
                result[col[0]] = self._deserialize_value(col[1])

        return result

    def _serialize_value(self, value: Any) -> Any:
        """Serialize Python value for Tablestore."""
        if isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, dict) or isinstance(value, list):
            return json.dumps(value)
        elif isinstance(value, bool):
            return 1 if value else 0
        return value

    def _deserialize_value(self, value: Any) -> Any:
        """Deserialize Tablestore value to Python."""
        if isinstance(value, str):
            # Try to parse as JSON
            try:
                if value.startswith('{') or value.startswith('['):
                    return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value

    # ==================== ADMIN OPERATIONS ====================

    def get_all_users(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all users (for admin). Limited to first N users."""
        from tablestore import INF_MIN, INF_MAX, Direction

        try:
            inclusive_start_primary_key = [('user_id', INF_MIN)]
            exclusive_end_primary_key = [('user_id', INF_MAX)]

            consumed, next_start_primary_key, row_list, next_token = self.client.get_range(
                'users',
                Direction.FORWARD,
                inclusive_start_primary_key,
                exclusive_end_primary_key,
                [],
                limit
            )

            users = [self._row_to_dict(row) for row in row_list]
            return users

        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
