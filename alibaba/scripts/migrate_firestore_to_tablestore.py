#!/usr/bin/env python3
"""
Migration script: Firestore → Tablestore
Migrates user data, trial requests, and logs from GCP Firestore to Alibaba Tablestore
"""
import os
import sys
import json
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# GCP Configuration
GCP_PROJECT = 'editorials-robot'

# Alibaba Configuration
TABLESTORE_ENDPOINT = os.environ.get(
    'TABLESTORE_ENDPOINT',
    'https://twbot-prod.eu-central-1.ots.aliyuncs.com'
)
TABLESTORE_INSTANCE = os.environ.get('TABLESTORE_INSTANCE', 'twbot-prod')
ALIBABA_ACCESS_KEY = os.environ.get('ALIBABA_ACCESS_KEY', '')
ALIBABA_SECRET_KEY = os.environ.get('ALIBABA_SECRET_KEY', '')


def init_firestore():
    """Initialize Firestore client"""
    try:
        from google.cloud import firestore
        return firestore.Client(project=GCP_PROJECT)
    except Exception as e:
        logger.error(f"Failed to initialize Firestore: {e}")
        return None


def init_tablestore():
    """Initialize Tablestore client"""
    try:
        from tablestore import OTSClient
        return OTSClient(
            TABLESTORE_ENDPOINT,
            ALIBABA_ACCESS_KEY,
            ALIBABA_SECRET_KEY,
            TABLESTORE_INSTANCE
        )
    except Exception as e:
        logger.error(f"Failed to initialize Tablestore: {e}")
        return None


def migrate_users(firestore_client, tablestore_client):
    """Migrate users collection"""
    logger.info("Migrating users...")
    from tablestore import Row, Condition, RowExistenceExpectation

    users_ref = firestore_client.collection('users')
    docs = users_ref.stream()

    count = 0
    for doc in docs:
        user_data = doc.to_dict()
        user_id = doc.id

        # Prepare row for Tablestore
        primary_key = [('user_id', str(user_id))]

        # Convert data to Tablestore format
        attribute_columns = []

        # Balance (convert to integer minutes * 100 for precision)
        balance = user_data.get('balance_minutes', 0)
        if isinstance(balance, float):
            balance = int(balance * 100)  # Store as cents for precision
        attribute_columns.append(('balance_minutes', int(balance)))

        # Trial status
        trial_status = user_data.get('trial_status', 'none')
        attribute_columns.append(('trial_status', str(trial_status)))

        # User name
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        user_name = f"{first_name} {last_name}".strip()
        attribute_columns.append(('user_name', user_name))

        # Timestamps
        created_at = user_data.get('created_at')
        if created_at:
            attribute_columns.append(('created_at', str(created_at)))

        last_activity = user_data.get('last_activity')
        if last_activity:
            attribute_columns.append(('last_activity', str(last_activity)))

        # Settings (as JSON)
        settings = user_data.get('settings', {})
        attribute_columns.append(('settings', json.dumps(settings)))

        # Write to Tablestore
        try:
            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.IGNORE)
            tablestore_client.put_row('users', row, condition)
            count += 1
            logger.debug(f"Migrated user {user_id}")
        except Exception as e:
            logger.error(f"Failed to migrate user {user_id}: {e}")

    logger.info(f"Migrated {count} users")
    return count


def migrate_trial_requests(firestore_client, tablestore_client):
    """Migrate trial_requests collection"""
    logger.info("Migrating trial requests...")
    from tablestore import Row, Condition, RowExistenceExpectation

    ref = firestore_client.collection('trial_requests')
    docs = ref.stream()

    count = 0
    for doc in docs:
        data = doc.to_dict()
        user_id = doc.id

        primary_key = [('user_id', str(user_id))]
        attribute_columns = [
            ('status', str(data.get('status', 'pending'))),
            ('user_name', str(data.get('user_name', ''))),
            ('request_timestamp', str(data.get('request_timestamp', datetime.now().isoformat()))),
        ]

        try:
            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.IGNORE)
            tablestore_client.put_row('trial_requests', row, condition)
            count += 1
        except Exception as e:
            logger.error(f"Failed to migrate trial request {user_id}: {e}")

    logger.info(f"Migrated {count} trial requests")
    return count


def migrate_payment_logs(firestore_client, tablestore_client):
    """Migrate payment_logs collection"""
    logger.info("Migrating payment logs...")
    from tablestore import Row, Condition, RowExistenceExpectation
    import uuid

    ref = firestore_client.collection('payment_logs')
    docs = ref.stream()

    count = 0
    for doc in docs:
        data = doc.to_dict()
        payment_id = doc.id or str(uuid.uuid4())

        primary_key = [('payment_id', str(payment_id))]
        attribute_columns = [
            ('user_id', str(data.get('user_id', ''))),
            ('amount', int(data.get('amount', 0))),
            ('stars_amount', int(data.get('stars_amount', 0))),
            ('minutes_added', int(data.get('minutes_added', 0))),
            ('timestamp', str(data.get('timestamp', datetime.now().isoformat()))),
        ]

        charge_id = data.get('telegram_payment_charge_id')
        if charge_id:
            attribute_columns.append(('telegram_payment_charge_id', str(charge_id)))

        try:
            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.IGNORE)
            tablestore_client.put_row('payment_logs', row, condition)
            count += 1
        except Exception as e:
            logger.error(f"Failed to migrate payment {payment_id}: {e}")

    logger.info(f"Migrated {count} payment logs")
    return count


def migrate_transcription_logs(firestore_client, tablestore_client, days=30):
    """Migrate recent transcription_logs"""
    logger.info(f"Migrating transcription logs (last {days} days)...")
    from tablestore import Row, Condition, RowExistenceExpectation
    from datetime import timedelta
    import uuid

    ref = firestore_client.collection('transcription_logs')
    cutoff = datetime.now() - timedelta(days=days)

    # Query recent logs
    docs = ref.where('timestamp', '>=', cutoff).stream()

    count = 0
    for doc in docs:
        data = doc.to_dict()
        log_id = doc.id or str(uuid.uuid4())

        primary_key = [('log_id', str(log_id))]
        attribute_columns = [
            ('user_id', str(data.get('user_id', ''))),
            ('timestamp', str(data.get('timestamp', datetime.now().isoformat()))),
            ('duration', int(data.get('duration', 0))),
            ('char_count', int(data.get('char_count', 0))),
            ('status', str(data.get('status', 'completed'))),
        ]

        try:
            row = Row(primary_key, attribute_columns)
            condition = Condition(RowExistenceExpectation.IGNORE)
            tablestore_client.put_row('transcription_logs', row, condition)
            count += 1
        except Exception as e:
            logger.error(f"Failed to migrate log {log_id}: {e}")

    logger.info(f"Migrated {count} transcription logs")
    return count


def verify_migration(tablestore_client):
    """Verify data was migrated correctly"""
    logger.info("Verifying migration...")
    from tablestore import Direction, INF_MIN, INF_MAX

    tables = ['users', 'trial_requests', 'payment_logs', 'transcription_logs']
    results = {}

    for table in tables:
        try:
            # Count rows in table
            inclusive_start = [('user_id', INF_MIN)] if 'user' in table else [
                (f'{table.rstrip("s")}_id', INF_MIN)
            ]
            exclusive_end = [('user_id', INF_MAX)] if 'user' in table else [
                (f'{table.rstrip("s")}_id', INF_MAX)
            ]

            # For simplicity, just check if table exists and has data
            consumed, next_start, row_list, next_token = tablestore_client.get_range(
                table,
                Direction.FORWARD,
                inclusive_start,
                exclusive_end,
                limit=1
            )
            results[table] = 'OK' if row_list else 'Empty'
        except Exception as e:
            results[table] = f'Error: {e}'

    for table, status in results.items():
        logger.info(f"  {table}: {status}")

    return results


def main():
    """Main migration function"""
    logger.info("=" * 50)
    logger.info("Starting Firestore → Tablestore Migration")
    logger.info("=" * 50)

    # Initialize clients
    firestore_client = init_firestore()
    if not firestore_client:
        logger.error("Cannot proceed without Firestore client")
        sys.exit(1)

    tablestore_client = init_tablestore()
    if not tablestore_client:
        logger.error("Cannot proceed without Tablestore client")
        sys.exit(1)

    # Run migrations
    results = {
        'users': migrate_users(firestore_client, tablestore_client),
        'trial_requests': migrate_trial_requests(firestore_client, tablestore_client),
        'payment_logs': migrate_payment_logs(firestore_client, tablestore_client),
        'transcription_logs': migrate_transcription_logs(firestore_client, tablestore_client),
    }

    # Verify
    verify_migration(tablestore_client)

    # Summary
    logger.info("=" * 50)
    logger.info("Migration Summary:")
    for collection, count in results.items():
        logger.info(f"  {collection}: {count} records")
    logger.info("=" * 50)

    return results


if __name__ == '__main__':
    main()
