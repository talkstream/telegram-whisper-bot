#!/usr/bin/env python3
"""
Deploy script for Alibaba Cloud Function Compute 3.0
Uses alibabacloud-fc20230330 SDK
"""
import os
import sys
import base64
import zipfile
import io
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
alibaba_dir = os.path.dirname(script_dir)


def get_credentials():
    """Get credentials from aliyun CLI config."""
    config_path = os.path.expanduser('~/.aliyun/config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            profiles = config.get('profiles', [])
            current_profile = config.get('current', 'default')

            for profile in profiles:
                if profile.get('name') == current_profile:
                    return {
                        'access_key_id': profile.get('access_key_id'),
                        'access_key_secret': profile.get('access_key_secret'),
                        'region': profile.get('region_id', 'eu-central-1')
                    }
    return None


def create_zip_package(source_dir: str) -> bytes:
    """Create zip package from source directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d not in ['__pycache__', '.git', 'node_modules']]

            for file in files:
                if file.endswith(('.pyc', '.zip', '.b64', '.DS_Store')):
                    continue

                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zf.write(file_path, arcname)

    return buffer.getvalue()


def deploy_function(function_name: str, source_dir: str, region: str = 'eu-central-1'):
    """Deploy function code to FC 3.0."""
    from alibabacloud_fc20230330.client import Client as FCClient
    from alibabacloud_fc20230330 import models as fc_models
    from alibabacloud_tea_openapi import models as open_api_models

    print(f"  -> Deploying {function_name}...")

    # Get credentials
    creds = get_credentials()
    if not creds:
        print("     ✗ Could not get credentials")
        return False

    # Create FC client
    config = open_api_models.Config(
        access_key_id=creds['access_key_id'],
        access_key_secret=creds['access_key_secret'],
        region_id=region
    )
    # Use account-specific endpoint for FC 3.0
    config.endpoint = f'5907469887573677.{region}.fc.aliyuncs.com'
    client = FCClient(config)

    # Create zip package
    zip_data = create_zip_package(source_dir)
    zip_base64 = base64.b64encode(zip_data).decode('utf-8')
    print(f"     Package size: {len(zip_data) / 1024:.1f} KB")

    # Update function code using FC 3.0 API
    try:
        request = fc_models.UpdateFunctionRequest(
            body=fc_models.UpdateFunctionInput(
                code=fc_models.InputCodeLocation(
                    zip_file=zip_base64
                )
            )
        )

        response = client.update_function(function_name, request)
        print(f"     ✓ {function_name} deployed successfully")
        print(f"       checksum: {response.body.code_checksum[:16]}...")
        return True

    except Exception as e:
        error_msg = str(e)
        if 'does not exist' in error_msg:
            print(f"     ✗ Function does not exist: {function_name}")
        else:
            print(f"     ✗ Failed: {error_msg[:200]}")
        return False


def main():
    print("=== Alibaba Cloud Function Compute 3.0 Deployment ===\n")

    region = 'eu-central-1'
    service = 'telegram-whisper-bot-prod'

    functions = [
        ('webhook-handler', os.path.join(alibaba_dir, 'webhook-handler')),
        ('audio-processor', os.path.join(alibaba_dir, 'audio-processor')),
    ]

    print(f"Region: {region}")
    print(f"Service: {service}\n")

    success = True
    for func_name, source_dir in functions:
        # FC 3.0 uses full function name: service$function
        full_name = f"{service}${func_name}"
        if not deploy_function(full_name, source_dir, region):
            success = False

    print("\n=== Deployment complete! ===")
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
