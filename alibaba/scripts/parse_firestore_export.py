#!/usr/bin/env python3
"""
Parse Firestore export files and migrate to Tablestore
"""
import os
import struct
import re
from tablestore import OTSClient, Row, Condition, RowExistenceExpectation

# Alibaba credentials - set via environment variables
TABLESTORE_ENDPOINT = os.environ.get('TABLESTORE_ENDPOINT', 'https://twbot-prod.eu-central-1.ots.aliyuncs.com')
ALIBABA_ACCESS_KEY = os.environ.get('ALIBABA_ACCESS_KEY', '')
ALIBABA_SECRET_KEY = os.environ.get('ALIBABA_SECRET_KEY', '')
TABLESTORE_INSTANCE = os.environ.get('TABLESTORE_INSTANCE', 'twbot-prod')

EXPORT_DIR = '/tmp/firestore-export/backup-20260204/all_namespaces'


def parse_record_file(filepath):
    """Parse a Firestore export record file and extract documents"""
    documents = []

    with open(filepath, 'rb') as f:
        data = f.read()

    # Find document patterns
    # User IDs are numeric strings after "users\""
    user_pattern = rb'users"[\x05-\x10](\d+)'
    matches = re.finditer(user_pattern, data)

    for match in matches:
        user_id = match.group(1).decode('utf-8')
        start_pos = match.start()

        # Find the document data after this user ID
        # Look for field patterns within a reasonable range
        doc_data = data[start_pos:start_pos + 500]  # Approximate document size

        doc = {'user_id': user_id}

        # Parse balance_minutes - look for double value
        balance_match = re.search(rb'balance_minutes.{5,20}!\x00{0,7}(.{8})', doc_data)
        if balance_match:
            try:
                balance_bytes = balance_match.group(1)
                balance = struct.unpack('<d', balance_bytes)[0]
                doc['balance_minutes'] = int(balance) if balance == int(balance) else balance
            except:
                doc['balance_minutes'] = 0

        # Parse trial_status
        trial_match = re.search(rb'trial_status.{3,10}\x1a[\x05-\x10](\w+)', doc_data)
        if trial_match:
            doc['trial_status'] = trial_match.group(1).decode('utf-8', errors='ignore')

        # Parse first_name (may contain UTF-8 Cyrillic)
        name_match = re.search(rb'first_name.{3,10}\x1a[\x05-\x20](.{2,30}?)[\x00\x7a\x08]', doc_data)
        if name_match:
            try:
                name = name_match.group(1).decode('utf-8', errors='ignore').strip()
                # Clean up non-printable characters
                name = ''.join(c for c in name if c.isprintable())
                doc['first_name'] = name
            except:
                pass

        # Parse micro_package_purchases (integer)
        mpp_match = re.search(rb'micro_package_purchases.{3,10}\x08(\x00|\x01|\x02|\x03|\x04|\x05)', doc_data)
        if mpp_match:
            doc['micro_package_purchases'] = mpp_match.group(1)[0] if mpp_match.group(1) else 0

        documents.append(doc)

    # Remove duplicates (keep last occurrence)
    unique_docs = {}
    for doc in documents:
        unique_docs[doc['user_id']] = doc

    return list(unique_docs.values())


def migrate_users_to_tablestore(users):
    """Migrate parsed users to Tablestore"""
    client = OTSClient(
        TABLESTORE_ENDPOINT,
        ALIBABA_ACCESS_KEY,
        ALIBABA_SECRET_KEY,
        TABLESTORE_INSTANCE
    )

    count = 0
    for user in users:
        user_id = user.get('user_id')
        if not user_id:
            continue

        primary_key = [('user_id', str(user_id))]

        # Build attribute columns
        attr_cols = []

        balance = user.get('balance_minutes', 0)
        # Store balance as integer (minutes * 100 for cents precision)
        if isinstance(balance, float):
            attr_cols.append(('balance_minutes', int(balance * 100)))
        else:
            attr_cols.append(('balance_minutes', int(balance) * 100))

        trial_status = user.get('trial_status', 'none')
        attr_cols.append(('trial_status', str(trial_status)))

        first_name = user.get('first_name', '')
        attr_cols.append(('user_name', str(first_name)))

        # Serialize settings
        import json
        settings = {'use_code_tags': False, 'use_yo': True}
        attr_cols.append(('settings', json.dumps(settings)))

        try:
            row = Row(primary_key, attr_cols)
            condition = Condition(RowExistenceExpectation.IGNORE)
            client.put_row('users', row, condition)
            count += 1
            print(f"✓ Migrated user {user_id}: {first_name}, balance={balance}")
        except Exception as e:
            print(f"✗ Failed user {user_id}: {e}")

    return count


def main():
    print("=" * 50)
    print("Firestore → Tablestore Migration")
    print("=" * 50)

    # Parse users
    users_file = os.path.join(EXPORT_DIR, 'kind_users', 'output-0')
    if os.path.exists(users_file):
        print(f"\nParsing {users_file}...")
        users = parse_record_file(users_file)
        print(f"Found {len(users)} users")

        for user in users[:5]:
            print(f"  Sample: {user}")

        # Migrate
        print("\nMigrating to Tablestore...")
        migrated = migrate_users_to_tablestore(users)
        print(f"\n✅ Migrated {migrated} users")
    else:
        print(f"File not found: {users_file}")


if __name__ == '__main__':
    main()
