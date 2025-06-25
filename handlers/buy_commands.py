"""
Buy package command handlers for Telegram Whisper Bot
"""

import logging
from .base import BaseHandler


class BuyMicroCommandHandler(BaseHandler):
    """Handler for /buy_micro command"""
    
    def handle(self, update_data):
        user_id = update_data['user_id']
        chat_id = update_data['chat_id']
        user_data = update_data['user_data']
        send_invoice = self.services['telegram_service'].send_invoice
        send_message = self.services['telegram_service'].send_message
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        # Check if user can buy micro package
        micro_purchases = user_data.get('micro_package_purchases', 0) if user_data else 0
        micro_package_info = PRODUCT_PACKAGES.get("micro_10")
        can_buy_micro = micro_purchases < micro_package_info.get("purchase_limit", 3) if micro_package_info else False
        
        if can_buy_micro:
            send_invoice(chat_id, micro_package_info['title'], micro_package_info['description'], 
                        micro_package_info['payload'], "XTR", [{"label": "Stars", "amount": micro_package_info['stars_amount']}])
        else:
            send_message(chat_id, "❌ Вы уже исчерпали лимит покупок промо-пакета 'Микро'. Выберите другой пакет с помощью команды /buy_minutes")
        
        return "OK", 200


class BuyStartCommandHandler(BaseHandler):
    """Handler for /buy_start command"""
    
    def handle(self, update_data):
        chat_id = update_data['chat_id']
        send_invoice = self.services['telegram_service'].send_invoice
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        package = PRODUCT_PACKAGES.get("start_50")
        if package:
            send_invoice(chat_id, package['title'], package['description'], 
                        package['payload'], "XTR", [{"label": "Stars", "amount": package['stars_amount']}])
        
        return "OK", 200


class BuyStandardCommandHandler(BaseHandler):
    """Handler for /buy_standard command"""
    
    def handle(self, update_data):
        chat_id = update_data['chat_id']
        send_invoice = self.services['telegram_service'].send_invoice
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        package = PRODUCT_PACKAGES.get("standard_200")
        if package:
            send_invoice(chat_id, package['title'], package['description'], 
                        package['payload'], "XTR", [{"label": "Stars", "amount": package['stars_amount']}])
        
        return "OK", 200


class BuyProfiCommandHandler(BaseHandler):
    """Handler for /buy_profi command"""
    
    def handle(self, update_data):
        chat_id = update_data['chat_id']
        send_invoice = self.services['telegram_service'].send_invoice
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        package = PRODUCT_PACKAGES.get("profi_1000")
        if package:
            send_invoice(chat_id, package['title'], package['description'], 
                        package['payload'], "XTR", [{"label": "Stars", "amount": package['stars_amount']}])
        
        return "OK", 200


class BuyMaxCommandHandler(BaseHandler):
    """Handler for /buy_max command"""
    
    def handle(self, update_data):
        chat_id = update_data['chat_id']
        send_invoice = self.services['telegram_service'].send_invoice
        PRODUCT_PACKAGES = self.constants['PRODUCT_PACKAGES']
        
        package = PRODUCT_PACKAGES.get("max_8888")
        if package:
            send_invoice(chat_id, package['title'], package['description'], 
                        package['payload'], "XTR", [{"label": "Stars", "amount": package['stars_amount']}])
        
        return "OK", 200