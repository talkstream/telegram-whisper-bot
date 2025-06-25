"""
User command handlers for Telegram Whisper Bot
"""

import math
import logging
from datetime import datetime
import pytz

from .base import BaseHandler


class HelpCommandHandler(BaseHandler):
    """Handler for /help command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        send_message = self.services['telegram_service'].send_message
        
        help_text_user = """<b>Привет!</b> Я ваш бот-помощник для транскрибации аудио в текст с последующим форматированием.

<b>Как пользоваться:</b>
1. Просто перешлите аудиофайл или голосовое сообщение, либо пришлите файлом.
2. Можете отправить несколько файлов сразу - они будут обработаны по очереди.
3. Для работы сервиса вам необходимы минуты на балансе.

<b>Основные команды:</b>
• /start - Начать работу с ботом
• /help - Показать это сообщение
• /trial - Запросить пробный доступ (15 минут)

<b>Управление балансом:</b>
• /balance - Проверить текущий баланс
• /buy_minutes - Показать все доступные пакеты
• /buy_micro - Купить промо-пакет (10 минут за 10 ⭐)
• /buy_start - Купить пакет Старт (50 минут за 75 ⭐)
• /buy_standard - Купить пакет Стандарт (200 минут за 270 ⭐)
• /buy_profi - Купить пакет Профи (1000 минут за 1150 ⭐)
• /buy_max - Купить пакет MAX (8888 минут за 8800 ⭐)

<b>Настройки и статус:</b>
• /settings - Настройки форматирования вывода
• /code_on - Включить вывод с тегами &lt;code&gt;
• /code_off - Выключить теги &lt;code&gt;
• /batch (или /queue) - Просмотр ваших файлов в очереди

<b>Технические лимиты:</b>
• <b>Макс. размер файла:</b> 20 МБ
• <b>Форматы:</b> MP3, MP4, M4A, WAV, WEBM, OGG
• <b>Оптимальная длительность:</b> 7-8 минут

Для особых условий и корпоративных клиентов: @nafigator
"""
        if user_id == self.constants['OWNER_ID']:
            help_text_admin = """
<b>Админ-команды:</b>
• /user [поиск] - Поиск и управление пользователями
• /review_trials - Просмотр заявок на пробный доступ
• /credit &lt;user_id&gt; &lt;minutes&gt; - Начислить минуты пользователю
• /remove_user - Удалить пользователя из системы
• /stat - Статистика использования
• /cost - Расчет стоимости обработки
• /status - Статус очереди обработки (все пользователи)
• /flush - Очистить застрявшие задачи (>1 часа)
• /metrics [hours] - Метрики производительности (по умолчанию 24ч)
"""
            send_message(chat_id, help_text_user + help_text_admin, parse_mode="HTML")
        else:
            send_message(chat_id, help_text_user, parse_mode="HTML")
        return "OK", 200


class BalanceCommandHandler(BaseHandler):
    """Handler for /balance command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        get_user_data = self.services['get_user_data']
        send_message = self.services['telegram_service'].send_message
        stats_service = self.services.get('stats_service')
        
        # Always get fresh user data for balance
        fresh_user_data = get_user_data(user_id)
        if fresh_user_data:
            balance = fresh_user_data.get('balance_minutes', 0)
            balance_message = f"Ваш текущий баланс: {math.floor(balance)} минут."
            logging.info(f"Balance command: user {user_id} has {balance} minutes")
            
            avg_len_minutes = stats_service.get_average_audio_length_last_30_days(str(user_id)) if stats_service else None
            logging.info(f"Balance command: user {user_id} average length = {avg_len_minutes}")
            
            if avg_len_minutes is not None:
                balance_message += f"\nСредняя длина ваших аудио за последний месяц: {avg_len_minutes} мин."
            else:
                balance_message += "\nЗа последний месяц у вас не было успешных распознаваний для расчета средней длины."
            
            send_message(chat_id, balance_message)
        else:
            send_message(chat_id, "Вы еще не зарегистрированы. Пожалуйста, отправьте /start или /trial, чтобы запросить доступ.")
        
        return "OK", 200


class SettingsCommandHandler(BaseHandler):
    """Handler for /settings command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        user_data = update_data['user_data']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        
        logging.info(f"Processing /settings for user {user_id}")
        if not user_data:
            logging.warning(f"No user_data for {user_id}")
            send_message(chat_id, "Пожалуйста, сначала отправьте /start для регистрации.")
            return "OK", 200
        
        # Get current settings
        settings = firestore_service.get_user_settings(user_id) if firestore_service else {'use_code_tags': False}
        use_code_tags = settings.get('use_code_tags', False)
        
        # Create settings message with current state
        status_symbol = "✓" if use_code_tags else ""
        settings_msg = f"""<b>Настройки форматирования вывода</b>

Теги &lt;code&gt;: {'Включены' if use_code_tags else 'Выключены'} {status_symbol}

<b>Команды управления:</b>
• /code_on - Включить теги &lt;code&gt;
• /code_off - Выключить теги &lt;code&gt;

<i>Теги &lt;code&gt; отображают текст моноширинным шрифтом, что удобно для кода и технических текстов.</i>"""
        
        send_message(chat_id, settings_msg, parse_mode="HTML")
        return "OK", 200


class CodeOnCommandHandler(BaseHandler):
    """Handler for /code_on command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        
        if firestore_service:
            firestore_service.update_user_setting(user_id, 'use_code_tags', True)
            send_message(chat_id, "✅ Теги <code> включены. Теперь отформатированный текст будет отправляться с тегами для моноширинного шрифта.", parse_mode="HTML")
        else:
            send_message(chat_id, "Ошибка при сохранении настроек. Попробуйте позже.")
        return "OK", 200


class CodeOffCommandHandler(BaseHandler):
    """Handler for /code_off command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        
        if firestore_service:
            firestore_service.update_user_setting(user_id, 'use_code_tags', False)
            send_message(chat_id, "✅ Теги <code> выключены. Теперь отформатированный текст будет отправляться без тегов.", parse_mode="HTML")
        else:
            send_message(chat_id, "Ошибка при сохранении настроек. Попробуйте позже.")
        return "OK", 200


class TrialCommandHandler(BaseHandler):
    """Handler for /trial command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        user_name = update_data.get('user_name', f'User_{user_id}')
        create_trial_request = self.services['create_trial_request']
        send_message = self.services['telegram_service'].send_message
        
        status = create_trial_request(user_id, user_name)
        if status == True:
            send_message(chat_id, "✅ Ваша заявка на пробный доступ отправлена! Обычно мы рассматриваем заявки в течение 24 часов.")
        elif status == "already_pending":
            send_message(chat_id, "Вы уже подали заявку. Ожидайте рассмотрения.")
        elif status == "already_approved":
            send_message(chat_id, "Вам уже одобрен пробный доступ.")
        else:
            send_message(chat_id, "Ошибка при подаче заявки. Попробуйте позже.")
        return "OK", 200


class BuyMinutesCommandHandler(BaseHandler):
    """Handler for /buy_minutes and /top_up commands"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        user_name = update_data.get('user_name', f'User_{user_id}')
        user_data = update_data['user_data']
        send_message = self.services['telegram_service'].send_message
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        # Check if user can buy micro package
        micro_purchases = user_data.get('micro_package_purchases', 0) if user_data else 0
        micro_package_info = PRODUCT_PACKAGES.get("micro_10")
        can_buy_micro = micro_purchases < micro_package_info.get("purchase_limit", 3) if micro_package_info else False
        
        # Build package list message
        msg = "💰 <b>Доступные пакеты минут:</b>\n\n"
        
        # Show micro package if available
        if can_buy_micro:
            micro = PRODUCT_PACKAGES["micro_10"]
            remaining = micro["purchase_limit"] - micro_purchases
            price_per_min = micro['stars_amount'] / micro['minutes']
            msg += f"🎁 <b>{micro['title']}</b>\n"
            msg += f"   {micro['description']} - {micro['stars_amount']} ⭐\n"
            msg += f"   <i>≈ {price_per_min:.1f} ⭐ за минуту</i>\n"
            msg += f"   <i>Осталось покупок: {remaining}</i>\n"
            msg += "   Команда: /buy_micro\n\n"
        
        # Show all other packages
        start = PRODUCT_PACKAGES['start_50']
        price_per_min = start['stars_amount'] / start['minutes']
        msg += f"📦 <b>{start['title']}</b>\n"
        msg += f"   {start['description']} - {start['stars_amount']} ⭐\n"
        msg += f"   <i>≈ {price_per_min:.1f} ⭐ за минуту</i>\n"
        msg += "   Команда: /buy_start\n\n"
        
        standard = PRODUCT_PACKAGES['standard_200']
        price_per_min = standard['stars_amount'] / standard['minutes']
        msg += f"📦 <b>{standard['title']}</b>\n"
        msg += f"   {standard['description']} - {standard['stars_amount']} ⭐\n"
        msg += f"   <i>≈ {price_per_min:.2f} ⭐ за минуту</i>\n"
        msg += "   Команда: /buy_standard\n\n"
        
        profi = PRODUCT_PACKAGES['profi_1000']
        price_per_min = profi['stars_amount'] / profi['minutes']
        msg += f"📦 <b>{profi['title']}</b>\n"
        msg += f"   {profi['description']} - {profi['stars_amount']} ⭐\n"
        msg += f"   <i>≈ {price_per_min:.2f} ⭐ за минуту</i>\n"
        msg += "   Команда: /buy_profi\n\n"
        
        max_pkg = PRODUCT_PACKAGES['max_8888']
        price_per_min = max_pkg['stars_amount'] / max_pkg['minutes']
        msg += f"🚀 <b>{max_pkg['title']}</b>\n"
        msg += f"   {max_pkg['description']} - {max_pkg['stars_amount']} ⭐\n"
        msg += f"   <i>≈ {price_per_min:.2f} ⭐ за минуту</i>\n"
        msg += "   Команда: /buy_max\n\n"
        
        msg += "💡 <i>Выберите пакет и используйте соответствующую команду для покупки</i>"
        
        send_message(chat_id, msg, parse_mode="HTML")
        
        return "OK", 200


class QueueCommandHandler(BaseHandler):
    """Handler for /batch and /queue commands"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        firestore_service = self.services.get('firestore_service')
        send_message = self.services['telegram_service'].send_message
        set_user_state = self.services['set_user_state']
        UtilityService = self.services['UtilityService']
        db = self.services.get('db')
        
        # Check for actually pending/processing jobs for this user
        if firestore_service and db:
            from google.cloud.firestore_v1.base_query import FieldFilter
            user_jobs = db.collection('audio_jobs').where(
                filter=FieldFilter('user_id', '==', str(user_id))
            ).where(
                filter=FieldFilter('status', 'in', ['pending', 'processing'])
            ).stream()
            
            jobs_list = list(user_jobs)
            if not jobs_list:
                send_message(chat_id, "У вас нет файлов в очереди обработки.")
                # Clear old batch state
                set_user_state(user_id, None)
            else:
                queue_msg = "📋 <b>Ваши файлы в очереди:</b>\n\n"
                for idx, doc in enumerate(jobs_list, 1):
                    job_data = doc.to_dict()
                    status = job_data.get('status', 'unknown')
                    status_emoji = "⏳" if status == 'pending' else "⚙️"
                    duration = job_data.get('duration', 0)
                    queue_msg += f"{idx}. {status_emoji} {UtilityService.format_duration(duration)} - {status}\n"
                
                queue_msg += f"\n<b>Всего:</b> {UtilityService.pluralize_russian(len(jobs_list), 'файл', 'файла', 'файлов')} в очереди"
                send_message(chat_id, queue_msg, parse_mode="HTML")
        else:
            send_message(chat_id, "Информация о очереди недоступна.")
        
        return "OK", 200