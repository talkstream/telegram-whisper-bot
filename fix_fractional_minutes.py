#!/usr/bin/env python3
"""
Script to fix fractional minutes in user balances
Rounds up all fractional minutes to whole numbers
"""

import os
import math
from google.cloud import firestore

# Set up environment
PROJECT_ID = os.environ.get('GCP_PROJECT', 'editorials-robot')
DATABASE_ID = 'editorials-robot'

def fix_fractional_minutes():
    """Round up all user balances to whole minutes"""
    # Initialize Firestore
    db = firestore.Client(project=PROJECT_ID, database=DATABASE_ID)
    
    # Get all users
    users = db.collection('users').stream()
    
    updated_count = 0
    total_users = 0
    
    print("Checking user balances for fractional minutes...")
    
    for doc in users:
        total_users += 1
        user_id = doc.id
        user_data = doc.to_dict()
        
        # Get current balance
        current_balance = user_data.get('balance_minutes', 0)
        
        # Round up to nearest whole minute
        rounded_balance = math.ceil(current_balance)
        
        # Check if update is needed
        if current_balance != rounded_balance:
            # Update the balance
            doc.reference.update({'balance_minutes': rounded_balance})
            
            print(f"User {user_id} ({user_data.get('first_name', 'Unknown')}): "
                  f"{current_balance:.10f} → {rounded_balance} minutes")
            
            updated_count += 1
    
    print(f"\nCompleted! Updated {updated_count} out of {total_users} users.")

if __name__ == "__main__":
    fix_fractional_minutes()