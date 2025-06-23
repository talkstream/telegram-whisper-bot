import base64
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

def handle_pubsub_message(event, context):
    """Simple test handler for Pub/Sub messages"""
    try:
        # Log the incoming event
        logging.info(f"Received event: {event}")
        logging.info(f"Context: {context}")
        
        # Decode the Pub/Sub message
        if 'data' in event:
            pubsub_message = base64.b64decode(event['data']).decode('utf-8')
            job_data = json.loads(pubsub_message)
            logging.info(f"Decoded message: {job_data}")
        else:
            logging.warning("No data in event")
        
        return 'OK'
    except Exception as e:
        logging.error(f"Error in test handler: {e}")
        raise