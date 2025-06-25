"""
Command router for Telegram Whisper Bot
"""

import logging

from .user_commands import (
    HelpCommandHandler,
    BalanceCommandHandler,
    SettingsCommandHandler,
    CodeOnCommandHandler,
    CodeOffCommandHandler,
    TrialCommandHandler,
    BuyMinutesCommandHandler,
    QueueCommandHandler
)

from .admin_commands import (
    StatusCommandHandler,
    ReviewTrialsCommandHandler,
    RemoveUserCommandHandler,
    CostCommandHandler,
    FlushCommandHandler,
    StatCommandHandler,
    CreditCommandHandler,
    MetricsCommandHandler,
    UserSearchCommandHandler,
    ExportCommandHandler,
    ReportCommandHandler
)

from .buy_commands import (
    BuyMicroCommandHandler,
    BuyStartCommandHandler,
    BuyStandardCommandHandler,
    BuyProfiCommandHandler,
    BuyMaxCommandHandler
)


class CommandRouter:
    """Routes commands to appropriate handlers"""
    
    def __init__(self, services, constants):
        """
        Initialize router with services and constants
        
        Args:
            services: Dict containing service instances
            constants: Dict containing constants
        """
        self.services = services
        self.constants = constants
        
        # Initialize handlers
        self.handlers = {
            '/help': HelpCommandHandler(services, constants),
            '/balance': BalanceCommandHandler(services, constants),
            '/settings': SettingsCommandHandler(services, constants),
            '/code_on': CodeOnCommandHandler(services, constants),
            '/code_off': CodeOffCommandHandler(services, constants),
            '/trial': TrialCommandHandler(services, constants),
            '/buy_minutes': BuyMinutesCommandHandler(services, constants),
            '/top_up': BuyMinutesCommandHandler(services, constants),  # Alias
            '/batch': QueueCommandHandler(services, constants),
            '/queue': QueueCommandHandler(services, constants),  # Alias
            
            # Buy package commands
            '/buy_micro': BuyMicroCommandHandler(services, constants),
            '/buy_start': BuyStartCommandHandler(services, constants),
            '/buy_standard': BuyStandardCommandHandler(services, constants),
            '/buy_profi': BuyProfiCommandHandler(services, constants),
            '/buy_max': BuyMaxCommandHandler(services, constants),
            
            # Admin commands
            '/status': StatusCommandHandler(services, constants),
            '/review_trials': ReviewTrialsCommandHandler(services, constants),
            '/remove_user': RemoveUserCommandHandler(services, constants),
            '/cost': CostCommandHandler(services, constants),
            '/flush': FlushCommandHandler(services, constants),
            '/stat': StatCommandHandler(services, constants),
            '/credit': CreditCommandHandler(services, constants),
            '/metrics': MetricsCommandHandler(services, constants),
            '/user': UserSearchCommandHandler(services, constants),
            '/export': ExportCommandHandler(services, constants),
            '/report': ReportCommandHandler(services, constants),
        }
    
    def route(self, update_data):
        """
        Route command to appropriate handler
        
        Args:
            update_data: Dict containing parsed update data
                - text: Command text
                - user_id: User ID
                - chat_id: Chat ID
                - user_data: User data from database
                - message: Full message object
                
        Returns:
            Tuple of (response_text, status_code) or None if no handler found
        """
        text = update_data.get('text', '')
        
        # Extract command (first word)
        command = text.split()[0] if text else ''
        
        # Find handler
        handler = self.handlers.get(command)
        
        if handler:
            logging.info(f"Routing command {command} to {handler.__class__.__name__}")
            return handler.handle(update_data)
        
        # Check if it's a command with parameters that needs special handling
        if command == '/credit' and update_data.get('user_id') == self.constants['OWNER_ID']:
            return self.handlers['/credit'].handle(update_data)
        
        return None