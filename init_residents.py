#!/usr/bin/env python3
"""
Script to initialize resident data from INSERT INTO resident.sql file
Run this after deploying to Azure to populate the database
"""

import sqlite3
import os
import sys

def init_residents():
    """Load resident data from SQL file into the database"""
    
    # Database path
    db_path = 'instance/app.db'
    sql_file = 'INSERT INTO resident.sql'
    
    # Check if files exist
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        print("Please ensure the database is created first.")
        sys.exit(1)
    
    if not os.path.exists(sql_file):
        print(f"Error: SQL file not found at {sql_file}")
        sys.exit(1)
    
    try:
        # Connect to database
        print("Connecting to database...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Read SQL file
        print(f"Reading {sql_file}...")
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # Execute SQL
        print("Inserting resident data...")
        cursor.executescript(sql_content)
        
        # Commit changes
        conn.commit()
        
        # Verify data was inserted
        cursor.execute("SELECT COUNT(*) FROM resident")
        count = cursor.fetchone()[0]
        print(f"✓ Successfully inserted resident data!")
        print(f"✓ Total residents in database: {count}")
        
        conn.close()
        print("✓ Database connection closed.")
        print("\nResident data initialization complete!")
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("="*50)
    print("Resident Data Initialization")
    print("="*50)
    init_residents()
