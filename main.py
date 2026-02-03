import os
import logging
from fastapi import FastAPI
from telegram_bot_shared.services.utility import UtilityService
from app.initialization import services
from app.routes_fastapi import router

# Configure logging
UtilityService.setup_logging(component_name="bot")

# Initialize services (eager initialization)
try:
    if not services.initialize():
        logging.error("Failed to initialize services during startup")
except Exception as e:
    logging.error(f"Exception during service initialization: {e}")

app = FastAPI(
    title="Telegram Whisper Bot",
    description="Audio transcription bot backend",
    version="3.1.0"
)

app.include_router(router)

if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('PORT', 8080))
    uvicorn.run(app, host='0.0.0.0', port=port)
