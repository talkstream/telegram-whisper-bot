"""
Metrics command handler for performance monitoring
"""
import logging
from datetime import datetime
import pytz

from .base import BaseHandler


class MetricsCommandHandler(BaseHandler):
    """Handler for /metrics command (admin only)"""
    
    async def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        text = update_data.get('text', '')
        send_message = self.services['telegram_service'].send_message
        metrics_service = self.services.get('metrics_service')
        UtilityService = self.services['UtilityService']
        
        if user_id != self.constants['OWNER_ID']:
            return None  # Not authorized
            
        if not metrics_service:
            await send_message(chat_id, "Сервис метрик недоступен.")
            return "OK", 200
            
        try:
            # Parse hours parameter (default 24)
            parts = text.split()
            hours = 24
            if len(parts) > 1:
                try:
                    hours = int(parts[1])
                    hours = max(1, min(hours, 168))  # Between 1 and 168 hours (1 week)
                except ValueError:
                    pass
            
            # Get metrics summary
            summary = metrics_service.get_metrics_summary(hours)
            
            # Format the message
            msg = f"📊 <b>Метрики производительности за {hours} ч.</b>\n\n"
            
            # Processing stages
            stages = summary.get('processing_stages', {})
            if stages:
                msg += "⚙️ <b>Этапы обработки:</b>\n"
                for stage_name, stats in stages.items():
                    if stats['count'] > 0:
                        stage_display = self._get_stage_display_name(stage_name)
                        msg += f"\n{stage_display}:\n"
                        msg += f"  • Обработано: {stats['count']}\n"
                        msg += f"  • Среднее время: {stats['avg_duration']:.2f}с\n"
                        msg += f"  • Мин/Макс: {stats['min_duration']:.1f}с / {stats['max_duration']:.1f}с\n"
                        msg += f"  • Медиана: {stats['median_duration']:.1f}с\n"
                        msg += f"  • 95-й перцентиль: {stats['p95_duration']:.1f}с\n"
            
            # API performance
            api_stats = summary.get('api_performance', {})
            if api_stats:
                msg += "\n🌐 <b>Производительность API:</b>\n"
                for api_name, stats in api_stats.items():
                    msg += f"\n{api_name.upper()}:\n"
                    msg += f"  • Вызовов: {stats['total_calls']}\n"
                    msg += f"  • Успешность: {stats['success_rate']:.1f}%\n"
                    msg += f"  • Среднее время: {stats['avg_duration']:.2f}с\n"
                    msg += f"  • 95-й перцентиль: {stats['p95_duration']:.2f}с\n"
            
            # Queue statistics
            queue_stats = summary.get('queue_stats', {})
            if queue_stats:
                msg += "\n📋 <b>Статистика очереди:</b>\n"
                msg += f"  • В ожидании: {queue_stats['pending_jobs']}\n"
                msg += f"  • В обработке: {queue_stats['processing_jobs']}\n"
                msg += f"  • Среднее время ожидания: {UtilityService.format_duration(queue_stats['avg_wait_time_seconds'])}\n"
            
            # Error statistics
            error_stats = summary.get('error_stats', {})
            if error_stats and error_stats['total_jobs'] > 0:
                msg += "\n❌ <b>Статистика ошибок:</b>\n"
                msg += f"  • Успешно: {error_stats['successful_jobs']}\n"
                msg += f"  • С ошибками: {error_stats['failed_jobs']}\n"
                msg += f"  • Процент ошибок: {error_stats['error_rate_percent']:.1f}%\n"
            
            # Trim message if too long
            if len(msg) > 4000:
                msg = msg[:3900] + "\n\n<i>... сообщение обрезано ...</i>"
            
            await send_message(chat_id, msg, parse_mode="HTML")
            
        except Exception as e:
            logging.error(f"Error in metrics command: {e}")
            await send_message(chat_id, f"Ошибка при получении метрик: {str(e)}")
            
        return "OK", 200
    
    def _get_stage_display_name(self, stage_name: str) -> str:
        """Get display name for processing stage"""
        stage_names = {
            'download': '📥 Загрузка',
            'conversion': '🔄 Конвертация',
            'transcription': '🎙 Транскрипция',
            'formatting': '📝 Форматирование',
            'total_processing': '⏱ Общее время'
        }
        return stage_names.get(stage_name, stage_name)