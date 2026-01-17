from fastapi import APIRouter, Request, Header, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from typing import Optional
import logging
import json
import os

from app import logic
from app.initialization import services
from telegram_bot_shared.services.utility import UtilityService
from handlers.admin_commands import ReportCommandHandler

router = APIRouter()

@router.get("/_ah/warmup")
def warmup():
    """App Engine warmup handler"""
    elapsed = services.warmup()
    return f"Warmup completed in {elapsed:.2f} seconds"

@router.get("/health")
@router.get("/_ah/health")
def health():
    """Health check endpoint"""
    return "OK"

@router.post("/")
async def webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    """Main webhook handler for Telegram updates"""
    # Security check: Verify Telegram secret token
    webhook_secret = os.environ.get('TELEGRAM_WEBHOOK_SECRET')
    if webhook_secret:
        if x_telegram_bot_api_secret_token != webhook_secret:
            logging.warning(f"Unauthorized webhook attempt. Header: {x_telegram_bot_api_secret_token}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")

    if not services.initialized:
        if not services.initialize():
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Service initialization failed")
    
    try:
        update = await request.json()
        if not update:
            return "OK"
        
        logging.info(f"Received update: {json.dumps(update)}")
        
        # Handle different update types
        if 'message' in update:
            message = update['message']
            if 'successful_payment' in message:
                await run_in_threadpool(logic.handle_successful_payment, message, services)
            else:
                await run_in_threadpool(logic.handle_message, message, services)
        elif 'pre_checkout_query' in update:
            await run_in_threadpool(logic.handle_pre_checkout_query, update['pre_checkout_query'], services)
        elif 'callback_query' in update:
            await run_in_threadpool(logic.handle_callback_query, update['callback_query'], services)
        
        return "OK"
        
    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")

@router.get("/cleanup_stuck_jobs")
def cleanup_stuck_jobs():
    """Clean up stuck audio processing jobs"""
    if not services.initialized:
        services.initialize()
        
    return logic.cleanup_stuck_audio_jobs(services)

@router.get("/send_payment_notifications")
def send_payment_notifications():
    """Force send pending payment notifications"""
    if not services.initialized:
        services.initialize()
        
    services.notification_service._send_batched_payment_notifications()
    return "Payment notifications sent"

@router.get("/send_trial_notifications")
def send_trial_notifications():
    """Force check and send trial notifications"""
    if not services.initialized:
        services.initialize()
        
    services.notification_service.check_and_notify_trial_requests(force_check=True)
    return "Trial notifications checked"

@router.get("/send_scheduled_report")
def send_scheduled_report(type: str = 'daily'):
    """Send scheduled report (called by Cloud Scheduler)"""
    if not services.initialized:
        services.initialize()
        
    # Create a mock update_data for the report handler
    update_data = {
        'user_id': services.OWNER_ID,
        'chat_id': services.OWNER_ID,
        'text': f'/report {type}',
        'user_data': {'balance_minutes': 999999}  # Admin always has balance
    }
    
    # Use the existing ReportCommandHandler
    services_dict = services._create_services_dict()
    constants_dict = services._create_constants_dict()
    report_handler = ReportCommandHandler(services_dict, constants_dict)
    result = report_handler.handle(update_data)
    
    return f"Scheduled {type} report sent"
