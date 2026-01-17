"""
Stats Service - Statistics and analytics functions for the Telegram Whisper Bot
"""

import logging
import math
import pytz
from datetime import datetime, timedelta
from google.cloud.firestore_v1.base_query import FieldFilter
from typing import Dict, Any, Optional


class StatsService:
    """Service for statistics and analytics operations"""
    
    def __init__(self, db):
        self.db = db
    
    def get_stats_data(self, start_utc: datetime, end_utc: datetime) -> Dict[str, Any]:
        """Get transcription statistics for a given time period"""
        stats = {}
        
        try:
            query = self.db.collection('transcription_logs') \
                      .where(filter=FieldFilter('timestamp', '>=', start_utc)) \
                      .where(filter=FieldFilter('timestamp', '<', end_utc))
            docs = query.stream()
            
            for doc in docs:
                data = doc.to_dict()
                user_id_stat = data.get('user_id')
                if not user_id_stat: 
                    continue
                    
                if user_id_stat not in stats:
                    stats[user_id_stat] = {
                        'name': data.get('editor_name', f'ID_{user_id_stat}'),
                        'requests': 0,
                        'failures': 0,
                        'duration': 0,
                        'size': 0,
                        'chars': 0
                    }
                
                stats[user_id_stat]['requests'] += 1
                stats[user_id_stat]['duration'] += data.get('duration', 0)
                stats[user_id_stat]['size'] += data.get('file_size', 0)
                stats[user_id_stat]['chars'] += data.get('char_count', 0)
                
                if data.get('status') != 'success':
                    stats[user_id_stat]['failures'] += 1
                    
        except Exception as e:
            logging.error(f"Error getting stats data: {e}")
            
        return stats
    
    def get_average_audio_length_last_30_days(self, user_id_str: str) -> Optional[int]:
        """Calculate average audio length in minutes for a user over the last 30 days"""
        if not self.db: 
            return None
            
        utc_tz = pytz.utc
        now_utc = datetime.now(utc_tz)
        thirty_days_ago_utc = now_utc - timedelta(days=30)
        
        logging.info(f"AVG_LEN_LOG: Fetching logs for user {user_id_str} between {thirty_days_ago_utc} and {now_utc}")
        
        try:
            docs_query = self.db.collection('transcription_logs') \
                     .where(filter=FieldFilter('user_id', '==', user_id_str)) \
                     .where(filter=FieldFilter('timestamp', '>=', thirty_days_ago_utc)) \
                     .where(filter=FieldFilter('timestamp', '<=', now_utc)) \
                     .where(filter=FieldFilter('status', '==', 'success'))
            docs = docs_query.stream()
            
            retrieved_doc_timestamps = []
            total_duration = 0
            count = 0
            
            for doc in docs:
                data = doc.to_dict()
                # Use FFmpeg duration if available, otherwise fall back to duration
                doc_duration = data.get('ffmpeg_duration', data.get('duration', 0))
                retrieved_doc_timestamps.append(data.get('timestamp'))
                total_duration += doc_duration
                count += 1
                logging.info(f"AVG_LEN_LOG: Doc {count}: duration={doc_duration}s ({doc_duration/60:.1f}m), ffmpeg={data.get('ffmpeg_duration')}, telegram={data.get('telegram_duration')}")
            
            logging.info(f"AVG_LEN_LOG: Found {count} successful logs for user {user_id_str} in last 30 days. Total duration: {total_duration}s, Average: {total_duration/count if count > 0 else 0:.1f}s")
            
            if count > 0:
                avg_seconds = total_duration / count
                return math.floor(avg_seconds / 60)
                
        except Exception as e:
            logging.error(f"AVG_LEN_LOG: Error calculating average audio length for {user_id_str}: {e}")
        
        return None
    
    def get_active_users_count(self, days: int = 30) -> int:
        """Get count of active users in the last N days"""
        try:
            from datetime import datetime, timedelta
            import pytz
            
            now = datetime.now(pytz.utc)
            start_date = now - timedelta(days=days)
            
            # Query transcription logs for unique users
            docs = self.db.collection('transcription_logs') \
                         .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                         .where(filter=FieldFilter('status', '==', 'success')) \
                         .stream()
            
            unique_users = set()
            for doc in docs:
                data = doc.to_dict()
                user_id = data.get('user_id')
                if user_id:
                    unique_users.add(user_id)
            
            return len(unique_users)
        except Exception as e:
            logging.error(f"Error getting active users count: {e}")
            return 0
    
    def get_total_minutes_processed(self, days: int = 30) -> float:
        """Get total minutes processed in the last N days"""
        try:
            from datetime import datetime, timedelta
            import pytz
            
            now = datetime.now(pytz.utc)
            start_date = now - timedelta(days=days)
            
            docs = self.db.collection('transcription_logs') \
                         .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                         .where(filter=FieldFilter('status', '==', 'success')) \
                         .stream()
            
            total_seconds = 0
            for doc in docs:
                data = doc.to_dict()
                # Use FFmpeg duration if available
                duration = data.get('ffmpeg_duration', data.get('duration', 0))
                total_seconds += duration
            
            return round(total_seconds / 60, 2)
        except Exception as e:
            logging.error(f"Error getting total minutes processed: {e}")
            return 0.0
    
    def get_successful_transcriptions_count(self, days: int = 30) -> int:
        """Get count of successful transcriptions in the last N days"""
        try:
            from datetime import datetime, timedelta
            import pytz
            
            now = datetime.now(pytz.utc)
            start_date = now - timedelta(days=days)
            
            docs = self.db.collection('transcription_logs') \
                         .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                         .where(filter=FieldFilter('status', '==', 'success')) \
                         .stream()
            
            count = 0
            for doc in docs:
                count += 1
            
            return count
        except Exception as e:
            logging.error(f"Error getting successful transcriptions count: {e}")
            return 0
    
    def get_top_users_by_usage(self, limit: int = 10, days: int = 30) -> list:
        """Get top users by usage in the last N days"""
        try:
            from datetime import datetime, timedelta
            import pytz
            
            now = datetime.now(pytz.utc)
            start_date = now - timedelta(days=days)
            
            # Get transcription data
            docs = self.db.collection('transcription_logs') \
                         .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                         .where(filter=FieldFilter('status', '==', 'success')) \
                         .stream()
            
            user_stats = {}
            for doc in docs:
                data = doc.to_dict()
                user_id = data.get('user_id')
                if not user_id:
                    continue
                
                if user_id not in user_stats:
                    user_stats[user_id] = {
                        'name': data.get('editor_name', f'ID_{user_id}'),
                        'minutes': 0,
                        'count': 0
                    }
                
                duration = data.get('ffmpeg_duration', data.get('duration', 0))
                user_stats[user_id]['minutes'] += duration / 60
                user_stats[user_id]['count'] += 1
            
            # Sort by minutes and return top N
            sorted_users = sorted(
                user_stats.items(), 
                key=lambda x: x[1]['minutes'], 
                reverse=True
            )[:limit]
            
            return [
                {
                    'user_id': user_id,
                    'name': stats['name'],
                    'minutes': round(stats['minutes'], 2),
                    'count': stats['count']
                }
                for user_id, stats in sorted_users
            ]
        except Exception as e:
            logging.error(f"Error getting top users by usage: {e}")
            return []