"""
Admin command handlers for Telegram Whisper Bot
"""

import logging
import re
from datetime import datetime, timedelta
import pytz
import json
from google.cloud import firestore

from .base import BaseHandler


class StatusCommandHandler(BaseHandler):
    """Handler for /status command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        UtilityService = self.services['UtilityService']
        db = self.services.get('db')
        
        if user_id != self.constants['OWNER_ID']:
            return None  # Not authorized, continue to next handler
            
        if firestore_service and db:
            from google.cloud.firestore_v1.base_query import FieldFilter
            queue_count = firestore_service.count_pending_jobs()
            
            status_msg = "📊 <b>Статус очереди обработки</b>\n\n"
            if queue_count == 0:
                status_msg += "Очередь пуста."
            else:
                status_msg += f"Всего в очереди: {UtilityService.pluralize_russian(queue_count, 'файл', 'файла', 'файлов')}\n"
                
                # Show details about pending jobs
                pending_jobs = db.collection('audio_jobs').where(
                    filter=FieldFilter('status', 'in', ['pending', 'processing'])
                ).limit(10).stream()
                
                status_msg += "\nАктивные задачи:\n"
                for doc in pending_jobs:
                    job_data = doc.to_dict()
                    status_msg += f"• {job_data.get('user_name', 'Unknown')} - {job_data.get('status', 'unknown')}\n"
            
            send_message(chat_id, status_msg, parse_mode="HTML")
        else:
            send_message(chat_id, "Информация о очереди недоступна.")
        return "OK", 200


class ReviewTrialsCommandHandler(BaseHandler):
    """Handler for /review_trials command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        get_pending_trial_requests = self.services['get_pending_trial_requests']
        send_message = self.services['telegram_service'].send_message
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        requests = get_pending_trial_requests()
        if not requests:
            send_message(chat_id, "Нет ожидающих заявок на пробный доступ.")
        else:
            msg = "📋 Заявки на пробный доступ:\n\n"
            for req in requests:
                user_mention = f"<a href='tg://user?id={req['id']}'>{req['user_name']}</a>"
                msg += f"• {user_mention} (ID: {req['id']})\n"
                if req['timestamp']:
                    msg += f"  📅 Подана: {req['timestamp'].strftime('%d.%m.%Y %H:%M')}\n"
            msg += f"\nДля одобрения используйте:\n/credit USER_ID {self.constants['TRIAL_MINUTES']}"
            send_message(chat_id, msg, parse_mode="HTML")
        return "OK", 200


class RemoveUserCommandHandler(BaseHandler):
    """Handler for /remove_user command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        get_all_users_for_admin = self.services['get_all_users_for_admin']
        send_message = self.services['telegram_service'].send_message
        set_user_state = self.services['set_user_state']
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        users = get_all_users_for_admin()
        if not users:
            send_message(chat_id, "Нет пользователей в системе.")
        else:
            # Save user list in state for selection
            set_user_state(user_id, {'action': 'selecting_user_to_remove', 'users': users})
            
            msg = "👥 Выберите пользователя для удаления:\n\n"
            for idx, user in enumerate(users[:20], 1):  # Limit to 20 users
                msg += f"{idx}. {user['name']} (ID: {user['id']}) - {user['balance']} мин.\n"
            msg += "\nОтправьте номер пользователя или отмените командой /cancel"
            send_message(chat_id, msg)
        return "OK", 200


class CostCommandHandler(BaseHandler):
    """Handler for /cost command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        send_message = self.services['telegram_service'].send_message
        db = self.services.get('db')
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        if not db:
            send_message(chat_id, "База данных недоступна.")
            return "OK", 200
            
        from google.cloud.firestore_v1.base_query import FieldFilter
        # Calculate costs for the last 30 days
        now = datetime.now(pytz.utc)
        thirty_days_ago = now - timedelta(days=30)
        
        try:
            logs = db.collection('transcription_logs') \
                    .where(filter=FieldFilter('timestamp', '>=', thirty_days_ago)) \
                    .where(filter=FieldFilter('status', '==', 'success')) \
                    .stream()
            
            total_seconds = 0
            total_chars = 0
            count = 0
            
            for log in logs:
                data = log.to_dict()
                total_seconds += data.get('duration', 0)
                total_chars += data.get('char_count', 0)
                count += 1
            
            if count > 0:
                # OpenAI Whisper: $0.006 per minute
                whisper_cost = (total_seconds / 60) * 0.006
                
                # Gemini pricing (estimated)
                gemini_cost = (total_chars / 1000) * 0.00001  # Rough estimate
                
                # Infrastructure costs estimation
                # App Engine F2: ~$0.10/hour, min 1 instance 24/7 = $72/month
                # Assuming 30% utilization for bot operations
                app_engine_daily = (0.10 * 24 * 0.3)  # $0.72/day
                app_engine_cost = app_engine_daily * 30  # for 30 days
                
                # Cloud Functions: ~$0.00045 per audio file processed
                cloud_functions_cost = count * 0.00045
                
                # Firestore: ~$0.000014 per file (10 operations per file)
                firestore_cost = count * 0.000014
                
                # Other GCP services (Pub/Sub, Storage, etc)
                other_gcp_cost = count * 0.00001
                
                total_infra_cost = cloud_functions_cost + firestore_cost + other_gcp_cost
                total_api_cost = whisper_cost + gemini_cost
                total_cost = total_api_cost + total_infra_cost + app_engine_cost
                
                # Cost per minute calculations
                minutes_total = total_seconds / 60
                cost_per_minute_api = total_api_cost / minutes_total if minutes_total > 0 else 0
                cost_per_minute_infra = total_infra_cost / minutes_total if minutes_total > 0 else 0
                cost_per_minute_total = total_cost / minutes_total if minutes_total > 0 else 0
                
                cost_msg = f"""💰 <b>Расчет стоимости за последние 30 дней</b>

📊 Обработано: {count} файлов
⏱ Общая длительность: {total_seconds/60:.1f} минут
📝 Символов обработано: {total_chars:,}

💵 <b>API расходы:</b>
• Whisper API: ${whisper_cost:.2f}
• Gemini API: ${gemini_cost:.2f}
• <b>Итого API: ${total_api_cost:.2f}</b>

🏗 <b>Инфраструктура GCP:</b>
• App Engine (30% utilization): ${app_engine_cost:.2f}
• Cloud Functions: ${cloud_functions_cost:.2f}
• Firestore: ${firestore_cost:.2f}
• Прочие сервисы: ${other_gcp_cost:.2f}
• <b>Итого инфраструктура: ${total_infra_cost + app_engine_cost:.2f}</b>

💰 <b>ОБЩИЕ РАСХОДЫ: ${total_cost:.2f}</b>

📈 <b>Себестоимость:</b>
• API за минуту: ${cost_per_minute_api:.4f}
• Инфраструктура за минуту: ${cost_per_minute_infra:.4f}
• <b>Полная себестоимость за минуту: ${cost_per_minute_total:.4f}</b>
• Средняя стоимость на файл: ${total_cost/count:.3f}"""
                
                send_message(chat_id, cost_msg, parse_mode="HTML")
            else:
                send_message(chat_id, "Нет данных за последние 30 дней.")
                
        except Exception as e:
            logging.error(f"Error calculating costs: {e}")
            send_message(chat_id, f"Ошибка при расчете: {str(e)}")
        
        return "OK", 200


class FlushCommandHandler(BaseHandler):
    """Handler for /flush command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        UtilityService = self.services['UtilityService']
        db = self.services.get('db')
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        if not firestore_service or not db:
            send_message(chat_id, "Сервисы недоступны.")
            return "OK", 200
            
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter
            # Find stuck jobs (pending/processing for more than 1 hour)
            cutoff_time = datetime.now(pytz.utc) - timedelta(hours=1)
            
            stuck_jobs = db.collection('audio_jobs').where(
                filter=FieldFilter('status', 'in', ['pending', 'processing'])
            ).where(
                filter=FieldFilter('created_at', '<', cutoff_time)
            ).stream()
            
            stuck_list = list(stuck_jobs)
            
            if not stuck_list:
                send_message(chat_id, "✅ Нет застрявших задач в очереди.")
                return "OK", 200
            
            # Log details before deletion
            details_msg = f"🔍 Найдено {len(stuck_list)} застрявших задач:\n\n"
            total_duration = 0
            
            for doc in stuck_list:
                job_data = doc.to_dict()
                user_name = job_data.get('user_name', 'Unknown')
                job_user_id = job_data.get('user_id', 'Unknown')
                status = job_data.get('status', 'unknown')
                created_at = job_data.get('created_at')
                duration = job_data.get('duration', 0)
                
                details_msg += f"• User: {user_name} (ID: {job_user_id})\n"
                details_msg += f"  Status: {status}\n"
                if created_at:
                    details_msg += f"  Created: {created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                if duration:
                    details_msg += f"  Duration: {UtilityService.format_duration(duration)}\n"
                details_msg += f"  Doc ID: {doc.id}\n\n"
                
                total_duration += duration
            
            # Send details first
            send_message(chat_id, details_msg[:4000], parse_mode="HTML")  # Truncate if too long
            
            # Delete stuck jobs and refund minutes
            deleted_count = 0
            refunded_users = {}
            
            for doc in stuck_list:
                job_data = doc.to_dict()
                job_user_id = job_data.get('user_id')
                duration = job_data.get('duration', 0)
                
                # Delete the job
                doc.reference.delete()
                deleted_count += 1
                
                # Track refunds
                if job_user_id and duration > 0:
                    if job_user_id not in refunded_users:
                        refunded_users[job_user_id] = 0
                    refunded_users[job_user_id] += duration
            
            # Apply refunds
            for refund_user_id, total_seconds in refunded_users.items():
                minutes_to_refund = total_seconds / 60
                if firestore_service:
                    firestore_service.update_user_balance(int(refund_user_id), minutes_to_refund)
            
            # Send cleanup summary
            cleanup_msg = f"🧹 <b>Очистка завершена</b>\n\n"
            cleanup_msg += f"✅ Удалено задач: {deleted_count}\n"
            cleanup_msg += f"💰 Возвращено минут {len(refunded_users)} пользователям\n"
            if total_duration > 0:
                cleanup_msg += f"⏱ Общая длительность: {UtilityService.format_duration(total_duration)}\n"
            
            send_message(chat_id, cleanup_msg, parse_mode="HTML")
            
            logging.info(f"Flush command: Deleted {deleted_count} stuck jobs, refunded {len(refunded_users)} users")
            
        except Exception as e:
            logging.error(f"Error in flush command: {e}")
            send_message(chat_id, f"❌ Ошибка при очистке: {str(e)}")
        
        return "OK", 200


class StatCommandHandler(BaseHandler):
    """Handler for /stat command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        stats_service = self.services.get('stats_service')
        send_message = self.services['telegram_service'].send_message
        UtilityService = self.services['UtilityService']
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        if not stats_service:
            send_message(chat_id, "Сервис статистики недоступен.")
            return "OK", 200
            
        ranges = UtilityService.get_moscow_time_ranges()
        
        full_report = "📊 <b>Статистика использования сервиса:</b>\n\n"
        
        for period_name, (start_range, end_range) in ranges.items():
            period_stats = stats_service.get_stats_data(start_range, end_range) if stats_service else {}
            if period_stats:
                full_report += f"<b>{period_name}:</b>\n"
                sorted_stats = sorted(period_stats.items(), key=lambda x: x[1]['requests'], reverse=True)
                for user_id_str, data_stat in sorted_stats[:5]:  # Top 5 users
                    full_report += f"  • {data_stat['name']}:\n"
                    full_report += f"     - Запросов: {data_stat['requests']}"
                    if data_stat['failures'] > 0:
                        full_report += f" (неудач: {data_stat['failures']})"
                    full_report += "\n"
                    full_report += f"     - Общая длительность: {UtilityService.format_duration(data_stat['duration'])}\n"
                    if data_stat['requests'] > 0:
                        avg_duration_per_request = data_stat['duration'] / data_stat['requests']
                        full_report += f"     - Средняя длительность: {UtilityService.format_duration(avg_duration_per_request)}\n"
                    full_report += f"     - Размер: {UtilityService.format_size(data_stat['size'])}\n"
                    full_report += f"     - Символов: {data_stat['chars']:,}\n"
                full_report += "\n"
        
        send_message(chat_id, full_report[:4000], parse_mode="HTML")  # Telegram message limit
        return "OK", 200


class CreditCommandHandler(BaseHandler):
    """Handler for /credit command (admin only)"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        text = update_data['text']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        update_trial_request_status = self.services.get('update_trial_request_status')
        
        if user_id != self.constants['OWNER_ID']:
            return None
            
        # Parse command: /credit user_id minutes
        parts = text.split()
        if len(parts) != 3:
            send_message(chat_id, "Использование: /credit USER_ID MINUTES")
            return "OK", 200
            
        try:
            target_user_id = int(parts[1])
            minutes_to_add = float(parts[2])
            
            if firestore_service:
                # Update user balance
                firestore_service.update_user_balance(target_user_id, minutes_to_add)
                
                # If it's a trial approval (15 minutes), update trial status
                if minutes_to_add == self.constants['TRIAL_MINUTES']:
                    firestore_service.update_user_trial_status(target_user_id, 'approved')
                    if update_trial_request_status:
                        update_trial_request_status(target_user_id, 'approved', admin_comment='Approved via /credit command')
                
                send_message(chat_id, f"✅ Начислено {minutes_to_add} минут пользователю {target_user_id}")
                
                # Notify the user
                try:
                    if minutes_to_add == self.constants['TRIAL_MINUTES']:
                        user_msg = f"🎉 Поздравляем! Ваша заявка на пробный доступ одобрена. Вам начислено {int(minutes_to_add)} минут."
                    else:
                        user_msg = f"💰 Вам начислено {int(minutes_to_add)} минут администратором."
                    send_message(target_user_id, user_msg)
                except Exception as e:
                    logging.error(f"Failed to notify user {target_user_id}: {e}")
            else:
                send_message(chat_id, "Сервис базы данных недоступен.")
                
        except ValueError:
            send_message(chat_id, "Ошибка: USER_ID должен быть числом, MINUTES - числом.")
        except Exception as e:
            send_message(chat_id, f"Ошибка: {str(e)}")
            
        return "OK", 200