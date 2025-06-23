# Entry point for audio processor Cloud Function
from audio_processor import handle_pubsub_message

# Export the handler
__all__ = ['handle_pubsub_message']