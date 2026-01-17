import logging
from typing import Optional, Dict, Any, Union
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, FSInputFile

logger = logging.getLogger(__name__)

class AsyncTelegramService:
    """Async Service for interacting with Telegram Bot API using Aiogram"""
    
    def __init__(self, bot_token: str):
        self.bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        
    async def close(self):
        """Close the bot session"""
        await self.bot.session.close()

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "HTML", 
                    reply_markup: Optional[Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Dict]] = None) -> Any:
        """Send a message to a Telegram chat"""
        try:
            # Aiogram handles serialization of markup
            return await self.bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode=parse_mode, 
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error sending message to {chat_id}: {e}")
            return None
            
    async def edit_message_text(self, chat_id: int, message_id: int, text: str, 
                         parse_mode: str = "HTML", reply_markup: Optional[Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, Dict]] = None) -> Any:
        """Edit an existing message"""
        try:
            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error editing message {message_id} in {chat_id}: {e}")
            return None
            
    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message"""
        try:
            return await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"Error deleting message {message_id} in {chat_id}: {e}")
            return False
            
    async def send_document(self, chat_id: int, file_path: str, caption: str = "") -> Any:
        """Send a document to a Telegram chat"""
        try:
            file = FSInputFile(file_path)
            return await self.bot.send_document(chat_id=chat_id, document=file, caption=caption)
        except Exception as e:
            logger.error(f"Error sending document to {chat_id}: {e}")
            return None
            
    async def get_file_path(self, file_id: str) -> Optional[str]:
        """Get file path from Telegram servers"""
        try:
            file = await self.bot.get_file(file_id)
            return file.file_path
        except Exception as e:
            logger.error(f"Error getting file path for {file_id}: {e}")
            return None
            
    # download_file in aiogram usually downloads to buffer or path
    async def download_file(self, file_path: str, destination: str) -> Optional[str]:
        """Download file from Telegram servers"""
        try:
            await self.bot.download_file(file_path, destination)
            return destination
        except Exception as e:
            logger.error(f"Error downloading file {file_path}: {e}")
            return None
            
    async def answer_pre_checkout_query(self, query_id: str, ok: bool = True, error_message: str = "") -> bool:
        """Answer a pre-checkout query"""
        try:
            await self.bot.answer_pre_checkout_query(pre_checkout_query_id=query_id, ok=ok, error_message=error_message)
            return True
        except Exception as e:
            logger.error(f"Error answering pre_checkout_query {query_id}: {e}")
            return False

    async def send_invoice(self, chat_id: int, title: str, description: str, payload: str,
                    currency: str, prices: list, **kwargs) -> Any:
        """Send an invoice for Telegram Stars payment"""
        try:
            # Aiogram prices expects list of LabeledPrice objects or dicts?
            # In aiogram 3.x, prices is list[LabeledPrice]
            # Existing code passes dicts: [{"label": "Stars", "amount": 10}]
            # We need to convert them or check if aiogram accepts dicts.
            # Aiogram usually requires objects.
            from aiogram.types import LabeledPrice
            
            labeled_prices = [LabeledPrice(**p) if isinstance(p, dict) else p for p in prices]
            
            return await self.bot.send_invoice(
                chat_id=chat_id,
                title=title,
                description=description,
                payload=payload,
                currency=currency,
                prices=labeled_prices,
                **kwargs
            )
        except Exception as e:
            logger.error(f"Error sending invoice to {chat_id}: {e}")
            return None
    
    async def send_chat_action(self, chat_id: int, action: str) -> bool:
        try:
            await self.bot.send_chat_action(chat_id=chat_id, action=action)
            return True
        except Exception as e:
            logger.error(f"Error sending chat action to {chat_id}: {e}")
            return False
