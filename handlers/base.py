"""
Base handler class for command handlers
"""

class BaseHandler:
    """Base class for all command handlers"""
    
    def __init__(self, services, constants):
        """
        Initialize handler with services and constants
        
        Args:
            services: Dict containing service instances (firestore_service, stats_service, etc.)
            constants: Dict containing constants (OWNER_ID, TRIAL_MINUTES, etc.)
        """
        self.services = services
        self.constants = constants
        
    async def handle(self, update_data):
        """
        Handle the command
        
        Args:
            update_data: Dict containing parsed update data
                - user_id: User ID
                - chat_id: Chat ID  
                - text: Command text
                - user_data: User data from database
                - message: Full message object
                
        Returns:
            Tuple of (response_text, status_code) or None if command continues processing
        """
        raise NotImplementedError("Subclasses must implement handle method")