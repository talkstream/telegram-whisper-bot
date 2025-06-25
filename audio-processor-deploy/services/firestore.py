"""
Firestore Service - Centralized database operations for the Telegram Whisper Bot
"""
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


class FirestoreService:
    """Service for all Firestore database operations"""
    
    def __init__(self, project_id: str, database_id: str):
        """Initialize Firestore client"""
        self.project_id = project_id
        self.database_id = database_id
        self.db = firestore.Client(project=project_id, database=database_id)
        
    # --- User Management ---
    
    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user data by ID"""
        doc_ref = self.db.collection('users').document(str(user_id))
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else None
        
    def create_or_update_user(self, user_id: int, user_data: Dict[str, Any], merge: bool = False) -> None:
        """Create or update user data"""
        doc_ref = self.db.collection('users').document(str(user_id))
        if merge:
            doc_ref.update(user_data)
        else:
            doc_ref.set(user_data)
            
    def update_user_balance(self, user_id: int, minutes_to_add: float) -> Optional[Dict[str, Any]]:
        """Update user balance by adding minutes and return updated user data"""
        doc_ref = self.db.collection('users').document(str(user_id))
        doc = doc_ref.get()
        
        if doc.exists:
            # Update existing user
            doc_ref.update({'balance_minutes': firestore.Increment(minutes_to_add)})
        else:
            # Create new user with initial balance
            doc_ref.set({
                'first_name': f'User_{user_id}',
                'added_at': firestore.SERVER_TIMESTAMP,
                'balance_minutes': minutes_to_add,
                'micro_package_purchases': 0
            })
        
        # Return updated data
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else None
        
    def delete_user(self, user_id: int) -> None:
        """Delete user and all associated data"""
        # Delete user document
        self.db.collection('users').document(str(user_id)).delete()
        # Delete user state
        self.db.collection('user_states').document(str(user_id)).delete()
        # Delete trial request if exists
        trial_req_ref = self.db.collection('trial_requests').document(str(user_id))
        if trial_req_ref.get().exists:
            trial_req_ref.delete()
            
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users"""
        docs = self.db.collection('users').stream()
        users = []
        for doc in docs:
            data = doc.to_dict()
            users.append({
                'id': int(doc.id),
                'name': data.get('first_name', f'ID_{doc.id}'),
                'balance': data.get('balance_minutes', 0)
            })
        return users
        
    # --- User State Management ---
    
    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user state"""
        doc_ref = self.db.collection('user_states').document(str(user_id))
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else None
        
    def set_user_state(self, user_id: int, state_data: Any) -> None:
        """Set user state"""
        doc_ref = self.db.collection('user_states').document(str(user_id))
        if state_data:
            doc_ref.set(state_data if isinstance(state_data, dict) else {'state': state_data})
        else:
            doc_ref.delete()
            
    # --- Audio Job Management ---
    
    def create_audio_job(self, job_id: str, job_data: Dict[str, Any]) -> None:
        """Create audio processing job"""
        job_ref = self.db.collection('audio_jobs').document(job_id)
        job_ref.set(job_data)
        
    def update_audio_job(self, job_id: str, update_data: Dict[str, Any]) -> None:
        """Update audio job status"""
        doc_ref = self.db.collection('audio_jobs').document(job_id)
        update_data['updated_at'] = firestore.SERVER_TIMESTAMP
        doc_ref.update(update_data)
        
    def count_pending_jobs(self) -> int:
        """Count jobs that are pending or processing"""
        # Count only pending/processing jobs - don't count completed ones
        try:
            pending_count = 0
            
            # Count pending jobs
            pending_query = self.db.collection('audio_jobs').where(
                filter=FieldFilter('status', '==', 'pending')
            )
            pending_count += len(list(pending_query.stream()))
            
            # Count processing jobs
            processing_query = self.db.collection('audio_jobs').where(
                filter=FieldFilter('status', '==', 'processing')
            )
            pending_count += len(list(processing_query.stream()))
            
            return pending_count
        except Exception as e:
            logging.error(f"Error counting pending jobs: {e}")
            return 0
        
    def get_user_queue_position(self, user_id: int) -> Optional[int]:
        """Get user's position in the processing queue"""
        # Get all pending/processing jobs ordered by creation time
        query = self.db.collection('audio_jobs') \
                      .where(filter=FieldFilter('status', 'in', ['pending', 'processing'])) \
                      .order_by('created_at', direction=firestore.Query.ASCENDING)
        
        position = 0
        found = False
        for doc in query.stream():
            position += 1
            data = doc.to_dict()
            if str(data.get('user_id')) == str(user_id) and not found:
                found = True
                return position
                
        return None if not found else position
        
    def get_stuck_jobs(self, hours_threshold: int = 1) -> List[Tuple[str, Dict[str, Any]]]:
        """Get jobs that are stuck in pending/processing state for more than specified hours"""
        from datetime import timedelta
        import pytz
        
        # Calculate threshold timestamp
        threshold_time = datetime.now(pytz.utc) - timedelta(hours=hours_threshold)
        
        # Query for old pending/processing jobs
        stuck_jobs = []
        
        # Get pending jobs older than threshold
        pending_query = self.db.collection('audio_jobs') \
                             .where(filter=FieldFilter('status', 'in', ['pending', 'processing'])) \
                             .where(filter=FieldFilter('created_at', '<', threshold_time))
        
        for doc in pending_query.stream():
            data = doc.to_dict()
            stuck_jobs.append((doc.id, data))
            
        return stuck_jobs
        
    def delete_audio_job(self, job_id: str) -> None:
        """Delete an audio job"""
        self.db.collection('audio_jobs').document(job_id).delete()
        
    def cleanup_stuck_jobs(self, hours_threshold: int = 1) -> Tuple[int, List[Dict[str, Any]]]:
        """Clean up stuck jobs and return count and details of cleaned jobs"""
        stuck_jobs = self.get_stuck_jobs(hours_threshold)
        cleaned_jobs = []
        refunded_users = {}
        
        for job_id, job_data in stuck_jobs:
            # Store job info before deletion
            cleaned_jobs.append({
                'job_id': job_id,
                'user_id': job_data.get('user_id', 'Unknown'),
                'created_at': job_data.get('created_at'),
                'status': job_data.get('status'),
                'duration': job_data.get('duration', 0)
            })
            
            # Track refunds
            user_id = job_data.get('user_id')
            duration = job_data.get('duration', 0)
            if user_id and duration > 0:
                if user_id not in refunded_users:
                    refunded_users[user_id] = 0
                refunded_users[user_id] += duration
            
            # Delete the stuck job
            self.delete_audio_job(job_id)
        
        # Apply refunds
        for refund_user_id, total_seconds in refunded_users.items():
            minutes_to_refund = total_seconds / 60
            self.update_user_balance(int(refund_user_id), minutes_to_refund)
            
        return len(cleaned_jobs), cleaned_jobs
        
    # --- Trial Request Management ---
    
    def get_trial_request(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get trial request for user"""
        doc_ref = self.db.collection('trial_requests').document(str(user_id))
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else None
        
    def create_trial_request(self, user_id: int, request_data: Dict[str, Any]) -> None:
        """Create new trial request"""
        doc_ref = self.db.collection('trial_requests').document(str(user_id))
        doc_ref.set(request_data)
        
    def update_trial_request(self, user_id: int, update_data: Dict[str, Any]) -> None:
        """Update trial request"""
        doc_ref = self.db.collection('trial_requests').document(str(user_id))
        update_data['updated_at'] = firestore.SERVER_TIMESTAMP
        doc_ref.update(update_data)
        
    def get_pending_trial_requests(self, limit: Optional[int] = None) -> List[Tuple[str, Dict[str, Any]]]:
        """Get pending trial requests"""
        query = self.db.collection('trial_requests') \
                      .where(filter=FieldFilter('status', '==', 'pending')) \
                      .order_by('request_timestamp', direction=firestore.Query.ASCENDING)
        
        if limit:
            query = query.limit(limit)
            
        docs = query.stream()
        requests = []
        for doc in docs:
            data = doc.to_dict()
            requests.append((doc.id, {
                'user_name': data.get('user_name'),
                'user_id_str': data.get('user_id'),
                'timestamp': data.get('request_timestamp')
            }))
        return requests
        
    def get_all_pending_trial_requests(self) -> List[Any]:
        """Get all pending trial requests (for counting)"""
        return list(self.db.collection('trial_requests')
                          .where(filter=FieldFilter('status', '==', 'pending'))
                          .stream())
                          
    # --- Logging Operations ---
    
    def log_transcription(self, log_data: Dict[str, Any]) -> None:
        """Log transcription attempt"""
        log_ref = self.db.collection('transcription_logs').document()
        log_ref.set(log_data)
        
    def log_payment(self, payment_data: Dict[str, Any]) -> None:
        """Log payment transaction"""
        log_ref = self.db.collection('payment_logs').document()
        log_ref.set(payment_data)
        
    def log_oversized_file(self, file_data: Dict[str, Any]) -> None:
        """Log oversized file attempt"""
        log_ref = self.db.collection('oversized_files_log').document()
        log_ref.set(file_data)
        
    # --- Statistics Queries ---
    
    def get_transcription_stats(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get transcription statistics for date range"""
        query = self.db.collection('transcription_logs') \
                      .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                      .where(filter=FieldFilter('timestamp', '<=', end_date))
        
        docs = query.stream()
        stats = {}
        
        for doc in docs:
            data = doc.to_dict()
            user_id_stat = data.get('user_id')
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
                
        return list(stats.values())
        
    def get_user_transcriptions(self, user_id: int, start_date: datetime, 
                               end_date: datetime, limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int]:
        """Get user transcriptions for date range"""
        query = self.db.collection('transcription_logs') \
                      .where(filter=FieldFilter('user_id', '==', str(user_id))) \
                      .where(filter=FieldFilter('status', '==', 'success')) \
                      .where(filter=FieldFilter('timestamp', '>=', start_date)) \
                      .where(filter=FieldFilter('timestamp', '<=', end_date)) \
                      .order_by('timestamp', direction=firestore.Query.DESCENDING)
        
        if limit:
            query = query.limit(limit)
            
        docs = query.stream()
        transcriptions = []
        total_duration = 0
        
        for doc in docs:
            data = doc.to_dict()
            transcriptions.append(data)
            total_duration += data.get('duration', 0)
            
        return transcriptions, total_duration
        
    # --- Internal Bot State ---
    
    def get_last_trial_notification_timestamp(self) -> Optional[Any]:
        """Get last trial notification timestamp"""
        state_ref = self.db.collection('internal_bot_state').document('last_trial_notification_ts')
        state_doc = state_ref.get()
        if state_doc.exists:
            return state_doc.to_dict().get('timestamp')
        return None
        
    def update_last_trial_notification_timestamp(self, daily_check: bool = False) -> None:
        """Update last trial notification timestamp"""
        state_ref = self.db.collection('internal_bot_state').document('last_trial_notification_ts')
        data = {'timestamp': firestore.SERVER_TIMESTAMP}
        if daily_check:
            data['daily_check_done'] = True
        state_ref.set(data, merge=True)
        
    # --- Package Purchase Tracking ---
    
    def increment_micro_package_purchases(self, user_id: int, current_count: int) -> None:
        """Increment micro package purchase count"""
        doc_ref = self.db.collection('users').document(str(user_id))
        doc_ref.update({'micro_package_purchases': current_count + 1})
        
    # --- User Settings Management ---
    
    def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Get user settings with defaults"""
        user_data = self.get_user(user_id)
        if not user_data:
            return {'use_code_tags': False}  # Default settings - code tags disabled
            
        settings = user_data.get('settings', {})
        # Ensure default values for any missing settings
        defaults = {'use_code_tags': False}
        for key, value in defaults.items():
            if key not in settings:
                settings[key] = value
                
        return settings
        
    def update_user_setting(self, user_id: int, setting_name: str, value: Any) -> None:
        """Update a specific user setting"""
        doc_ref = self.db.collection('users').document(str(user_id))
        doc = doc_ref.get()
        
        if doc.exists:
            # Update nested settings field
            doc_ref.update({f'settings.{setting_name}': value})
        else:
            # Create user with settings if doesn't exist
            doc_ref.set({
                'first_name': f'User_{user_id}',
                'added_at': firestore.SERVER_TIMESTAMP,
                'settings': {setting_name: value}
            })
    
    def update_user_trial_status(self, user_id: int, status: str) -> None:
        """Update user trial status"""
        doc_ref = self.db.collection('users').document(str(user_id))
        doc_ref.update({'trial_status': status})