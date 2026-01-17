"""
Telegram Whisper Bot - Main Application
Refactored version: Lightweight Bot, Heavy Worker
"""

import os
import logging

from flask import Flask
from telegram_bot_shared.services.utility import UtilityService

# Import app modules
from app.initialization import services
from app.routes import register_routes

# Configure logging
UtilityService.setup_logging(component_name="bot")

# Create Flask app
app = Flask(__name__)

# Register all routes
register_routes(app, services)





if __name__ == '__main__':
    # Initialize services on startup
    if not services.initialize():
        logging.error("Failed to initialize services")
        exit(1)
    
    # Run Flask app (for local testing only)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)