"""
Metrics Service - Performance monitoring and metrics collection
"""
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import pytz
from google.cloud import firestore
from collections import defaultdict
import statistics


class MetricsService:
    """Service for collecting and analyzing performance metrics"""
    
    def __init__(self, db: firestore.Client):
        """Initialize metrics service with Firestore client"""
        self.db = db
        self.metrics_cache = defaultdict(list)
        self.active_timers = {}
        
    def start_timer(self, metric_name: str, job_id: str) -> float:
        """Start a timer for a specific metric"""
        start_time = time.time()
        timer_key = f"{metric_name}:{job_id}"
        self.active_timers[timer_key] = start_time
        return start_time
        
    def end_timer(self, metric_name: str, job_id: str) -> Optional[float]:
        """End a timer and record the duration"""
        timer_key = f"{metric_name}:{job_id}"
        start_time = self.active_timers.get(timer_key)
        
        if start_time is None:
            logging.warning(f"No timer found for {timer_key}")
            return None
            
        duration = time.time() - start_time
        del self.active_timers[timer_key]
        
        # Cache the metric
        self.metrics_cache[metric_name].append(duration)
        
        # Also log to Firestore for persistence
        self.log_metric(metric_name, job_id, duration)
        
        return duration
        
    def log_metric(self, metric_name: str, job_id: str, value: float, 
                   additional_data: Optional[Dict[str, Any]] = None) -> None:
        """Log a metric to Firestore"""
        try:
            metric_data = {
                'metric_name': metric_name,
                'job_id': job_id,
                'value': value,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'additional_data': additional_data or {}
            }
            
            self.db.collection('performance_metrics').add(metric_data)
        except Exception as e:
            logging.error(f"Failed to log metric {metric_name}: {e}")
            
    def log_api_call(self, api_name: str, duration: float, success: bool,
                     error: Optional[str] = None) -> None:
        """Log API call performance"""
        try:
            api_data = {
                'api_name': api_name,
                'duration': duration,
                'success': success,
                'error': error,
                'timestamp': firestore.SERVER_TIMESTAMP
            }
            
            self.db.collection('api_metrics').add(api_data)
        except Exception as e:
            logging.error(f"Failed to log API metric: {e}")
            
    def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get metrics summary for the specified time period"""
        now = datetime.now(pytz.utc)
        start_time = now - timedelta(hours=hours)
        
        summary = {
            'period_hours': hours,
            'start_time': start_time.isoformat(),
            'end_time': now.isoformat(),
            'processing_stages': {},
            'api_performance': {},
            'queue_stats': {},
            'error_stats': {}
        }
        
        try:
            # Get processing stage metrics
            stage_metrics = self._get_stage_metrics(start_time, now)
            summary['processing_stages'] = stage_metrics
            
            # Get API performance metrics
            api_metrics = self._get_api_metrics(start_time, now)
            summary['api_performance'] = api_metrics
            
            # Get queue statistics
            queue_stats = self._get_queue_stats()
            summary['queue_stats'] = queue_stats
            
            # Get error statistics
            error_stats = self._get_error_stats(start_time, now)
            summary['error_stats'] = error_stats
            
        except Exception as e:
            logging.error(f"Error getting metrics summary: {e}")
            
        return summary
        
    def _get_stage_metrics(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """Get processing stage performance metrics"""
        from google.cloud.firestore_v1.base_query import FieldFilter
        
        stages = ['download', 'conversion', 'transcription', 'formatting', 'total_processing']
        stage_stats = {}
        
        for stage in stages:
            try:
                query = self.db.collection('performance_metrics') \
                    .where(filter=FieldFilter('metric_name', '==', stage)) \
                    .where(filter=FieldFilter('timestamp', '>=', start_time)) \
                    .where(filter=FieldFilter('timestamp', '<=', end_time))
                    
                docs = query.stream()
                durations = [doc.to_dict()['value'] for doc in docs]
                
                if durations:
                    stage_stats[stage] = {
                        'count': len(durations),
                        'avg_duration': statistics.mean(durations),
                        'min_duration': min(durations),
                        'max_duration': max(durations),
                        'median_duration': statistics.median(durations),
                        'p95_duration': self._percentile(durations, 95) if len(durations) > 1 else durations[0]
                    }
                else:
                    stage_stats[stage] = {
                        'count': 0,
                        'avg_duration': 0,
                        'min_duration': 0,
                        'max_duration': 0,
                        'median_duration': 0,
                        'p95_duration': 0
                    }
                    
            except Exception as e:
                logging.error(f"Error getting metrics for stage {stage}: {e}")
                
        return stage_stats
        
    def _get_api_metrics(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """Get API performance metrics"""
        from google.cloud.firestore_v1.base_query import FieldFilter
        
        api_stats = {}
        
        try:
            query = self.db.collection('api_metrics') \
                .where(filter=FieldFilter('timestamp', '>=', start_time)) \
                .where(filter=FieldFilter('timestamp', '<=', end_time))
                
            docs = query.stream()
            
            # Group by API name
            api_data = defaultdict(lambda: {'durations': [], 'success_count': 0, 'error_count': 0})
            
            for doc in docs:
                data = doc.to_dict()
                api_name = data['api_name']
                api_data[api_name]['durations'].append(data['duration'])
                if data['success']:
                    api_data[api_name]['success_count'] += 1
                else:
                    api_data[api_name]['error_count'] += 1
                    
            # Calculate statistics
            for api_name, data in api_data.items():
                durations = data['durations']
                if durations:
                    api_stats[api_name] = {
                        'total_calls': len(durations),
                        'success_rate': data['success_count'] / len(durations) * 100,
                        'avg_duration': statistics.mean(durations),
                        'min_duration': min(durations),
                        'max_duration': max(durations),
                        'p95_duration': self._percentile(durations, 95) if len(durations) > 1 else durations[0]
                    }
                    
        except Exception as e:
            logging.error(f"Error getting API metrics: {e}")
            
        return api_stats
        
    def _get_queue_stats(self) -> Dict[str, Any]:
        """Get current queue statistics"""
        from google.cloud.firestore_v1.base_query import FieldFilter
        
        try:
            # Count pending jobs
            pending_count = len(list(
                self.db.collection('audio_jobs')
                .where(filter=FieldFilter('status', '==', 'pending'))
                .stream()
            ))
            
            # Count processing jobs
            processing_count = len(list(
                self.db.collection('audio_jobs')
                .where(filter=FieldFilter('status', '==', 'processing'))
                .stream()
            ))
            
            # Get average wait time for recently completed jobs
            one_hour_ago = datetime.now(pytz.utc) - timedelta(hours=1)
            completed_jobs = self.db.collection('audio_jobs') \
                .where(filter=FieldFilter('status', '==', 'completed')) \
                .where(filter=FieldFilter('updated_at', '>=', one_hour_ago)) \
                .limit(20) \
                .stream()
                
            wait_times = []
            for job in completed_jobs:
                data = job.to_dict()
                if 'created_at' in data and 'processing_started_at' in data:
                    wait_time = (data['processing_started_at'] - data['created_at']).total_seconds()
                    wait_times.append(wait_time)
                    
            avg_wait_time = statistics.mean(wait_times) if wait_times else 0
            
            return {
                'pending_jobs': pending_count,
                'processing_jobs': processing_count,
                'total_in_queue': pending_count + processing_count,
                'avg_wait_time_seconds': avg_wait_time
            }
            
        except Exception as e:
            logging.error(f"Error getting queue stats: {e}")
            return {
                'pending_jobs': 0,
                'processing_jobs': 0,
                'total_in_queue': 0,
                'avg_wait_time_seconds': 0
            }
            
    def _get_error_stats(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """Get error statistics"""
        from google.cloud.firestore_v1.base_query import FieldFilter
        
        try:
            # Count failed transcriptions
            failed_count = len(list(
                self.db.collection('transcription_logs')
                .where(filter=FieldFilter('status', '!=', 'success'))
                .where(filter=FieldFilter('timestamp', '>=', start_time))
                .where(filter=FieldFilter('timestamp', '<=', end_time))
                .stream()
            ))
            
            # Count successful transcriptions
            success_count = len(list(
                self.db.collection('transcription_logs')
                .where(filter=FieldFilter('status', '==', 'success'))
                .where(filter=FieldFilter('timestamp', '>=', start_time))
                .where(filter=FieldFilter('timestamp', '<=', end_time))
                .stream()
            ))
            
            total_count = failed_count + success_count
            error_rate = (failed_count / total_count * 100) if total_count > 0 else 0
            
            return {
                'failed_jobs': failed_count,
                'successful_jobs': success_count,
                'total_jobs': total_count,
                'error_rate_percent': error_rate
            }
            
        except Exception as e:
            logging.error(f"Error getting error stats: {e}")
            return {
                'failed_jobs': 0,
                'successful_jobs': 0,
                'total_jobs': 0,
                'error_rate_percent': 0
            }
            
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile of a list of values"""
        size = len(data)
        sorted_data = sorted(data)
        index = int(size * percentile / 100)
        if index >= size:
            index = size - 1
        return sorted_data[index]