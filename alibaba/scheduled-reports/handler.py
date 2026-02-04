"""
Scheduled Reports Handler for Telegram Whisper Bot
Triggered by Alibaba FC Timer Trigger (cron)
"""
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
import requests
from tablestore import OTSClient, INF_MIN, INF_MAX, Direction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def handler(event, context):
    """
    Timer trigger handler for scheduled reports.
    Triggered daily at 09:00 UTC (configure in s.yaml)
    """
    logger.info("Scheduled report triggered")

    # Get environment variables
    owner_id = os.environ.get('OWNER_ID')
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not owner_id or not bot_token:
        logger.error("Missing OWNER_ID or TELEGRAM_BOT_TOKEN")
        return {'statusCode': 500, 'body': 'Missing configuration'}

    # Initialize Tablestore client
    client = OTSClient(
        os.environ.get('TABLESTORE_ENDPOINT'),
        os.environ.get('ALIBABA_ACCESS_KEY'),
        os.environ.get('ALIBABA_SECRET_KEY'),
        os.environ.get('TABLESTORE_INSTANCE')
    )

    # Generate daily report
    report = generate_daily_report(client)

    # Send report to owner
    send_telegram_message(bot_token, owner_id, report)

    logger.info("Daily report sent successfully")
    return {'statusCode': 200, 'body': 'Report sent'}


def generate_daily_report(client) -> str:
    """Generate daily statistics report."""
    now = datetime.now(pytz.utc)
    yesterday = now - timedelta(days=1)

    # Get transcription logs for last 24 hours
    try:
        inclusive_start = [('log_id', INF_MIN)]
        exclusive_end = [('log_id', INF_MAX)]

        consumed, next_pk, rows, next_token = client.get_range(
            'transcription_logs',
            Direction.FORWARD,
            inclusive_start,
            exclusive_end,
            [],
            5000
        )

        # Filter by date and calculate stats
        total_transcriptions = 0
        total_duration = 0
        total_chars = 0
        unique_users = set()

        for row in rows:
            row_dict = _row_to_dict(row)
            timestamp_str = row_dict.get('timestamp', '')

            if timestamp_str:
                try:
                    ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    if ts >= yesterday:
                        total_transcriptions += 1
                        total_duration += row_dict.get('duration', 0)
                        total_chars += row_dict.get('char_count', 0)
                        unique_users.add(row_dict.get('user_id'))
                except (ValueError, TypeError):
                    pass

        # Get user stats
        user_start = [('user_id', INF_MIN)]
        user_end = [('user_id', INF_MAX)]

        consumed, next_pk, user_rows, next_token = client.get_range(
            'users',
            Direction.FORWARD,
            user_start,
            user_end,
            [],
            1000
        )

        total_users = len(user_rows)
        total_balance = sum(_row_to_dict(r).get('balance_minutes', 0) for r in user_rows)

        # Get payment stats
        pay_start = [('payment_id', INF_MIN)]
        pay_end = [('payment_id', INF_MAX)]

        consumed, next_pk, pay_rows, next_token = client.get_range(
            'payment_logs',
            Direction.FORWARD,
            pay_start,
            pay_end,
            [],
            1000
        )

        payments_24h = 0
        stars_24h = 0
        for row in pay_rows:
            row_dict = _row_to_dict(row)
            timestamp_str = row_dict.get('timestamp', '')
            if timestamp_str:
                try:
                    ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    if ts >= yesterday:
                        payments_24h += 1
                        stars_24h += row_dict.get('stars_amount', 0)
                except (ValueError, TypeError):
                    pass

    except Exception as e:
        logger.error(f"Error generating report: {e}")
        return f"❌ Ошибка генерации отчёта: {e}"

    # Format report
    report = (
        f"📊 <b>Ежедневный отчёт</b>\n"
        f"<i>{now.strftime('%Y-%m-%d %H:%M')} UTC</i>\n\n"
        f"<b>За последние 24 часа:</b>\n"
        f"• Транскрипций: {total_transcriptions}\n"
        f"• Обработано: {total_duration // 60} мин\n"
        f"• Символов: {total_chars:,}\n"
        f"• Активных пользователей: {len(unique_users)}\n"
        f"• Платежей: {payments_24h} ({stars_24h}⭐)\n\n"
        f"<b>Всего:</b>\n"
        f"• Пользователей: {total_users}\n"
        f"• Общий баланс: {total_balance} мин"
    )

    return report


def send_telegram_message(bot_token: str, chat_id: str, text: str):
    """Send message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    response = requests.post(url, json=payload, timeout=30)
    if not response.ok:
        logger.error(f"Failed to send Telegram message: {response.text}")
        raise Exception(f"Telegram API error: {response.status_code}")


def _row_to_dict(row) -> dict:
    """Convert Tablestore row to dictionary."""
    result = {}
    if row.primary_key:
        for pk in row.primary_key:
            result[pk[0]] = pk[1]
    if row.attribute_columns:
        for col in row.attribute_columns:
            result[col[0]] = col[1]
    return result
