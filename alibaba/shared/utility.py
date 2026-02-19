"""
Utility Service - General utility functions for the Telegram Whisper Bot
"""

import logging
import re
import time

import pytz
from datetime import datetime, timedelta, timezone


MUTE_FILE = '/tmp/twbot_mute_until'

# Trace context for correlation across services (FC processes one request at a time)
_trace_context = {}


def set_trace_context(trace_id=None, user_id=None):
    """Set trace context for the current request. All log records will include these fields."""
    if trace_id is not None:
        _trace_context['trace_id'] = trace_id
    if user_id is not None:
        _trace_context['user_id'] = str(user_id)


def get_trace_id():
    """Return current trace_id (for passing to downstream services)."""
    return _trace_context.get('trace_id', '')


class _TraceContextFilter(logging.Filter):
    """Injects trace_id and user_id into every log record."""
    def filter(self, record):
        record.trace_id = _trace_context.get('trace_id', '')
        record.user_id = _trace_context.get('user_id', '')
        return True


class TelegramErrorHandler(logging.Handler):
    """Logging handler that sends ERROR+ messages to Telegram owner.

    Includes a cooldown to avoid flooding (default 60s).
    Respects /mute command via a mute-file in /tmp.
    Never raises — notification failure must not break the app.
    """

    def __init__(self, bot_token: str, owner_id: int, component: str = "app",
                 cooldown: int = 60):
        super().__init__(level=logging.ERROR)
        self.bot_token = bot_token
        self.owner_id = owner_id
        self.component = component
        self.cooldown = cooldown
        self._last_sent = 0.0

    @staticmethod
    def is_muted() -> bool:
        """Check if error notifications are muted via /mute command."""
        import os
        if not os.path.exists(MUTE_FILE):
            return False
        try:
            with open(MUTE_FILE) as f:
                mute_until = float(f.read().strip())
            if time.time() < mute_until:
                return True
            os.unlink(MUTE_FILE)
        except Exception:
            pass
        return False

    @staticmethod
    def set_mute(hours: float):
        """Mute error notifications for N hours."""
        mute_until = time.time() + hours * 3600
        with open(MUTE_FILE, 'w') as f:
            f.write(str(mute_until))

    @staticmethod
    def clear_mute():
        """Remove mute."""
        import os
        if os.path.exists(MUTE_FILE):
            os.unlink(MUTE_FILE)

    def emit(self, record):
        now = time.time()
        if now - self._last_sent < self.cooldown:
            return
        if self.is_muted():
            return
        self._last_sent = now
        try:
            import requests
            text = f"\U0001f6a8 {record.levelname} [{self.component}]\n{record.name}: {record.getMessage()}"
            if len(text) > 4000:
                text = text[:4000] + "..."
            requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.owner_id, "text": text},
                timeout=3,
            )
        except Exception:
            pass


class UtilityService:
    """Service for general utility functions"""
    
    @staticmethod
    def setup_logging(component_name="app", bot_token=None, owner_id=None):
        """Configure structured JSON logging for Alibaba SLS.

        Respects LOG_LEVEL environment variable:
        - DEBUG: All messages (verbose)
        - INFO: Info and above (default for development)
        - WARNING: Warnings and above (recommended for production)
        - ERROR: Errors only

        If bot_token and owner_id are provided, ERROR+ logs are also
        sent to the owner via Telegram (with 60s cooldown).
        """
        import sys
        import os
        import logging

        logger = logging.getLogger()

        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        handler = logging.StreamHandler(sys.stdout)

        try:
            # v3+: pythonjsonlogger.json; legacy: pythonjsonlogger.jsonlogger
            try:
                from pythonjsonlogger.json import JsonFormatter as _BaseJsonFormatter
            except ImportError:
                from pythonjsonlogger.jsonlogger import JsonFormatter as _BaseJsonFormatter

            class SLSJsonFormatter(_BaseJsonFormatter):
                def add_fields(self, log_record, record, message_dict):
                    super(SLSJsonFormatter, self).add_fields(log_record, record, message_dict)

                    # Map standard python levels to SLS severity
                    if not log_record.get('severity'):
                        log_record['severity'] = record.levelname

                    # Add timestamp if not present
                    if not log_record.get('timestamp'):
                        now = datetime.now(timezone.utc)
                        log_record['timestamp'] = now.isoformat()

                    # Add component
                    if not log_record.get('component'):
                        log_record['component'] = component_name

            formatter = SLSJsonFormatter('%(timestamp)s %(severity)s %(name)s %(message)s %(component)s %(trace_id)s %(user_id)s')
        except ImportError:
            # Fallback to standard logging if python-json-logger not installed
            formatter = logging.Formatter(
                f'%(asctime)s [%(levelname)s] [{component_name}] %(name)s: %(message)s'
            )

        handler.setFormatter(formatter)
        handler.addFilter(_TraceContextFilter())
        logger.handlers = [handler]

        # Set log level from environment variable (default: INFO for backward compatibility)
        log_level_str = os.environ.get('LOG_LEVEL', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        logger.setLevel(log_level)

        # Quiet down some noisy libraries (always at WARNING or higher)
        logging.getLogger('google.cloud').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)

        # Telegram error notifications
        if bot_token and owner_id:
            tg_handler = TelegramErrorHandler(bot_token, int(owner_id), component_name)
            logger.addHandler(tg_handler)

    @staticmethod
    def format_duration(seconds):
        """Format seconds into HH:MM:SS format"""
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    
    @staticmethod
    def format_size(bytes_size):
        """Format bytes into human readable size"""
        if bytes_size < 1024: 
            return f"{bytes_size} B"
        elif bytes_size < 1024**2: 
            return f"{bytes_size/1024:.1f} KB"
        elif bytes_size < 1024**3: 
            return f"{bytes_size/1024**2:.1f} MB"
        else: 
            return f"{bytes_size/1024**3:.1f} GB"
    
    @staticmethod
    def pluralize_russian(number, one, two_four, many):
        """
        Правильное склонение существительных с числительными в русском языке
        number: число
        one: форма для 1 (файл)
        two_four: форма для 2-4 (файла)
        many: форма для 5+ (файлов)
        """
        if number % 10 == 1 and number % 100 != 11:
            return f"{number} {one}"
        elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
            return f"{number} {two_four}"
        else:
            return f"{number} {many}"
    
    @staticmethod
    def escape_html(text):
        """Escape HTML special characters for Telegram HTML parse mode"""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;'))
    
    @staticmethod
    def get_first_sentence(text):
        """Extract first sentence from text"""
        if not text: 
            return ""
        match = re.search(r'^.*?[.!?](?=\s|$)', text, re.DOTALL)
        return match.group(0) if match else text.split('\n')[0]
    
    @staticmethod
    def get_moscow_time_str():
        """Get current Moscow time as formatted string"""
        moscow_tz = pytz.timezone("Europe/Moscow")
        now_utc = datetime.now(timezone.utc)
        now_moscow = now_utc.astimezone(moscow_tz)
        return now_moscow.strftime("%d.%m.%Y %H:%M:%S MSK")
    
    @staticmethod
    def get_moscow_time_ranges():
        """Get time ranges for statistics (today, week, month, year) in Moscow timezone"""
        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        utc_tz = pytz.utc
        
        # Today
        today_start_msk = now_moscow.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_msk = today_start_msk + timedelta(days=1)
        
        # This week
        week_start_msk = today_start_msk - timedelta(days=now_moscow.weekday())
        week_end_msk = week_start_msk + timedelta(days=7)
        
        # This month
        month_start_msk = today_start_msk.replace(day=1)
        next_month_calc = month_start_msk.replace(day=28) + timedelta(days=4)
        month_end_msk = next_month_calc.replace(day=1)
        
        # This year
        year_start_msk = today_start_msk.replace(month=1, day=1)
        next_year_calc = year_start_msk.replace(year=year_start_msk.year + 1)
        year_end_msk = next_year_calc
        
        return {
            "Сегодня": (today_start_msk.astimezone(utc_tz), today_end_msk.astimezone(utc_tz)),
            "Эта неделя": (week_start_msk.astimezone(utc_tz), week_end_msk.astimezone(utc_tz)),
            "Этот месяц": (month_start_msk.astimezone(utc_tz), month_end_msk.astimezone(utc_tz)),
            "Этот год": (year_start_msk.astimezone(utc_tz), year_end_msk.astimezone(utc_tz)),
        }