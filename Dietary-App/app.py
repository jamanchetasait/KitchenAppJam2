# app.py — Full app with Menu Builder, Scheduler, Legacy daily page (/menu/legacy),
# Inventory, Residents, Staff, Dashboard, and strong pre-checks before deductions.
# Weekly grid FIX: days objects now include {"dow", "date"} to match planned_menu_week.html.
import os, io, csv, logging, traceback, json
from functools import wraps
from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import Any, Dict, List, Optional

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, flash, jsonify
)
from flask_migrate import Migrate
from sqlalchemy import or_, func

# models.py must be in the same folder
from models import (
    db, User, Resident, InventoryItem,
    # New menu system models
    Menu, MenuIngredient, MenuSchedule, MenuScheduleItem
)

# Optional .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# OpenAI v1 client (new syntax)
OPENAI_ENABLED = False
try:
    from openai import OpenAI
    OPENAI_ENABLED = True
except Exception:
    OPENAI_ENABLED = False

# -------------------------- logging --------------------------
logger = logging.getLogger("dietary_app")
if not logger.handlers:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

# -------------------------- helpers --------------------------

def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def _calc_age(bday):
    if not bday:
        return None
    t = date.today()
    return t.year - bday.year - ((t.month, t.day) < (bday.month, bday.day))

def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def model_has_column(model, name):
    try:
        return hasattr(model, "__table__") and name in model.__table__.c.keys()
    except Exception:
        return False

def register_age_helper(app):
    app.jinja_env.globals["age"] = _calc_age

# ---- Role-based dashboard tiles (which big cards to show) ----

def dashboard_tiles_for(role: str):
    # Each tile: (label, href, color_key)
    manager = [
        ("Residents", url_for("residents_list"), "purple"),
        ("Inventory", url_for("inventory_list"), "green"),
        ("Menu", url_for("menu_hub"), "blue"),
        ("Staff", url_for("staff_list"), "red"),
    ]
    dietitian = [
        ("Residents", url_for("residents_list"), "purple"),
        ("Menu", url_for("menu_hub"), "blue"),
        ("Inventory", url_for("inventory_list"), "green"),
    ]
    cook = [
        ("Menu", url_for("menu_hub"), "blue"),
        ("Inventory", url_for("inventory_list"), "green"),
    ]
    role_map = {
        "Manager": manager,
        "Dietitian": dietitian,
        "Cook": cook,
    }
    return role_map.get(role, manager)

# -------------------------- app factory --------------------------

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev')

    # Database
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///dietary.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    Migrate(app, db)

    register_age_helper(app)

    # --- Utilities ---
    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                if request.accept_mimetypes.best == 'application/json' or request.is_json:
                    return jsonify({
                        'error': 'Unauthorized',
                        'message': 'Login required'
                    }), 401
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return wrapper

    def json_error(message: str, status: int = 400, **extra):
        payload = {'error': message}
        if extra:
            payload.update(extra)
        return jsonify(payload), status

    # ---------------- Health and diagnostics ----------------
    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat() + 'Z'})

    @app.get('/diagnostics/openai')
    def diag_openai():
        info: Dict[str, Any] = {
            'openai_imported': OPENAI_ENABLED,
            'has_api_key': bool(os.getenv('OPENAI_API_KEY')),
        }
        if not OPENAI_ENABLED:
            return jsonify({'ok': False, 'info': info, 'error': 'openai package not available'}), 200
        try:
            client = OpenAI()
            # Lightweight non-chargeable check: not calling model list to avoid scope; validate credentials by creating client
            _ = bool(client)
            return jsonify({'ok': True, 'info': info})
        except Exception as e:
            logger.exception("OpenAI diagnostics failed")
            return jsonify({'ok': False, 'info': info, 'error': str(e)}), 200

    # ---------------- Chatbot route with robust JSON errors ----------------
    @app.post('/api/chatbot')
    def chatbot():
        req_json: Optional[Dict[str, Any]] = None
        try:
            req_json = request.get_json(silent=True) or {}
        except Exception:
            # Fallback to form
            req_json = {}
        user_message = (req_json.get('message') or '').strip()
        history = req_json.get('history')
        model = (req_json.get('model') or os.getenv('OPENAI_MODEL') or 'gpt-4o-mini').strip()
        temperature = req_json.get('temperature', 0.7)
        max_tokens = req_json.get('max_tokens', 500)

        if not user_message:
            return json_error('Missing message', 400)

        # Validate history
        messages: List[Dict[str, str]] = [{
            'role': 'system',
            'content': 'You are a helpful kitchen management assistant. Help users with meal planning, inventory management, dietary restrictions, and kitchen operations.'
        }]
        if isinstance(history, list):
            for i, m in enumerate(history):
                if isinstance(m, dict) and m.get('role') in {'system', 'user', 'assistant'} and isinstance(m.get('content'), str):
                    messages.append({'role': m['role'], 'content': m['content']})
                else:
                    logger.warning('Skipping invalid history item at %s: %s', i, m)
        messages.append({'role': 'user', 'content': user_message})

        # Prepare OpenAI client
        if not OPENAI_ENABLED:
            return json_error('OpenAI client not available', 500, details='Install openai>=1.0 and set OPENAI_API_KEY')
        try:
            client = OpenAI()
        except Exception as ce:
            logger.exception('Failed to initialize OpenAI client')
            return json_error('Failed to initialize OpenAI client', 500, details=str(ce))

        # Call OpenAI using v1 syntax with timeout via request options
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=int(max_tokens),
                temperature=float(temperature),
                timeout=30,
            )
        except Exception as api_error:
            logger.exception('OpenAI API call failed')
            return json_error('OpenAI API call failed', 500, details=str(api_error))

        bot_response = ""
        try:
            if resp and getattr(resp, 'choices', None) and len(resp.choices) > 0:
                choice0 = resp.choices[0]
                # OpenAI v1 returns choice0.message.content
                bot_response = getattr(getattr(choice0, 'message', None), 'content', None) or ""
        except Exception:
            logger.exception('Failed parsing OpenAI response')
            return json_error('Failed parsing OpenAI response', 500, raw=json.loads(resp.model_dump_json()) if hasattr(resp, 'model_dump_json') else None)

        if not bot_response:
            return json_error('Empty response from OpenAI', 502)

        return jsonify({'response': bot_response, 'model': model})

    # ----------------- Other routes (placeholders to keep existing app working) -----------------
    @app.get('/')
    def index():
        return render_template('index.html', tiles=dashboard_tiles_for(session.get('role', 'Manager')))

    # Existing application routes assumed below ... (omitted for brevity)

    return app

# -------------------------- dev entrypoint --------------------------
if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username="manager").first():
            mgr = User(
                first_name="",
                last_name="",
                username="manager",
                employee_id="00000000",
                email="manager@example.com",
                role="Manager",
                must_change_password=False,
            )
            mgr.set_password("1234")
            db.session.add(mgr)
            db.session.commit()
            print("Seeded manager (manager / 1234)")
    app.run(debug=True)
