"""
Utility Service - General utility functions for the Telegram Whisper Bot
"""

import re
import pytz
from datetime import datetime, timedelta


class UtilityService:
    """Service for general utility functions"""
    
    @staticmethod
    def setup_logging(component_name="app"):
        """Configure structured JSON logging for Google Cloud"""
        import sys
        import logging
        from pythonjsonlogger import jsonlogger

        class StackdriverJsonFormatter(jsonlogger.JsonFormatter):
            def add_fields(self, log_record, record, message_dict):
                super(StackdriverJsonFormatter, self).add_fields(log_record, record, message_dict)
                
                # Map standard python levels to Stackdriver severity
                if not log_record.get('severity'):
                    log_record['severity'] = record.levelname
                
                # Add timestamp if not present
                if not log_record.get('timestamp'):
                    now = datetime.utcnow().replace(tzinfo=pytz.utc)
                    log_record['timestamp'] = now.isoformat()
                
                # Add component
                if not log_record.get('component'):
                    log_record['component'] = component_name

        logger = logging.getLogger()
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        handler = logging.StreamHandler(sys.stdout)
        formatter = StackdriverJsonFormatter('%(timestamp)s %(severity)s %(name)s %(message)s %(component)s %(trace_id)s %(user_id)s')
        handler.setFormatter(formatter)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        
        # Quiet down some noisy libraries
        logging.getLogger('google.cloud').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)

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
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
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