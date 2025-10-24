import os
import sqlite3
from app import create_app
from models import db, User, Resident

application = create_app()

with application.app_context():
    db.create_all()
    print('Database tables created')
    
    if not User.query.filter_by(username='manager').first():
        mgr = User(username='manager', employee_id='00000000', email='manager@example.com', role='Manager', must_change_password=False)
        mgr.set_password('1234')
        db.session.add(mgr)
        db.session.commit()
        print('Created manager account')
    
    if Resident.query.count() == 0:
        sql_file = os.path.join(os.path.dirname(__file__), 'INSERT INTO resident.sql')
        if os.path.exists(sql_file):
            try:
                db_uri = application.config['SQLALCHEMY_DATABASE_URI']
                if db_uri.startswith('sqlite:///'):
                    db_path = db_uri.replace('sqlite:///', '')
                    conn = sqlite3.connect(db_path)
                    with open(sql_file, 'r') as f:
                        conn.executescript(f.read())
                    conn.commit()
                    conn.close()
                    print('Loaded resident data from SQL')
            except Exception as e:
                print(f'Error loading SQL: {e}')
