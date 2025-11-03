# app.py — Full app with Menu Builder, Scheduler, Legacy daily page (/menu/legacy),
# Inventory, Residents, Staff, Dashboard, and strong pre-checks before deductions.
# Weekly grid FIX: days objects now include {"dow", "date"} to match planned_menu_week.html.
import os, io, csv
from functools import wraps
from datetime import datetime, timedelta, date
from collections import defaultdict
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, flash, jsonify
)
from flask_migrate import Migrate
from sqlalchemy import or_, func   # func used by CSV export filters
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
        ("Inventory", url_for("inventory_list"), "green"),
        ("Menu", url_for("menu_hub"), "blue"),
    ]
    cook = [
        ("Residents", url_for("residents_list"), "purple"),
        ("Inventory", url_for("inventory_list"), "green"),
        ("Menu", url_for("menu_hub"), "blue"),
    ]
    aide = [
        ("Residents", url_for("residents_list"), "purple"),
        ("Inventory", url_for("inventory_list"), "green"),
        ("Menu", url_for("planned_menus"), "blue"),
    ]
    mapping = {
        "Manager": manager,
        "Dietitian": dietitian,
        "Cook": cook,
        "Dietary Aide": aide,
    }
    return mapping.get(role or "", aide)

# -------------------------- factory --------------------------
def create_app():
    app = Flask(__name__)
    sqlite_uri = "sqlite:///" + os.path.join(os.getcwd(), "instance", "app.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", sqlite_uri)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-key")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    os.makedirs(os.path.join(os.getcwd(), "instance"), exist_ok=True)
    db.init_app(app)
    Migrate(app, db)
    register_age_helper(app)

    INVENTORY_UNITS = [
        "kg", "g", "bags", "cases", "dozen", "cans", "liters", "jugs",
        "bunches", "heads", "loaves", "packs", "bottles", "jars", "boxes", "pcs"
    ]
    ROLES = ["Manager", "Cook", "Dietitian", "Dietary Aide"]

    @app.context_processor
    def inject_current_user():
        return {"current_user": session.get("user")}

    # ---------------- guards ----------------
    def login_required(f):
        @wraps(f)
        def w(*a, **kw):
            if "user" not in session:
                return redirect(url_for("login"))
            return f(*a, **kw)
        return w

    def current_role():
        return session.get("user", {}).get("role")

    def roles_required(*roles):
        def decorate(f):
            @wraps(f)
            def wrapped(*a, **kw):
                r = current_role()
                if r not in roles:
                    flash("You do not have access to that page.", "error")
                    return redirect(url_for("dashboard"))
                return f(*a, **kw)
            return wrapped
        return decorate

    @app.before_request
    def enforce_pw_change():
        allowed = {"login", "logout", "change_password", "static"}
        u = session.get("user")
        if not u:
            return
        obj = User.query.get(u["id"])
        if obj and getattr(obj, "must_change_password", False):
            if request.endpoint not in allowed:
                return redirect(url_for("change_password"))

    # ---------------- auth ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip().lower()
            password = (request.form.get("password") or "").strip()
            user = User.query.filter(
                or_(User.username.ilike(username), User.employee_id.ilike(username))
            ).first()
            if user and user.check_password(password):
                session["user"] = {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                    "first_name": getattr(user, "first_name", "") or "",
                    "last_name": getattr(user, "last_name", "") or "",
                }
                if getattr(user, "must_change_password", False):
                    return redirect(url_for("change_password"))
                return redirect(url_for("dashboard"))
            flash("Invalid credentials.", "error")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    def change_password():
        uinfo = session.get("user")
        if not uinfo:
            return redirect(url_for("login"))
        user = User.query.get_or_404(uinfo["id"])
        error = None
        if request.method == "POST":
            new = (request.form.get("new_password") or "")
            cfm = (request.form.get("confirm_password") or "")
            if new != cfm:
                error = "Passwords do not match."
            elif len(new) < 8:
                error = "Password must be at least 8 characters."
            else:
                user.set_password(new)
                user.must_change_password = False
                db.session.commit()
                flash("Password updated.", "success")
                return redirect(url_for("dashboard"))
        return render_template("change_password.html", error=error)

    # ---------------- home / dashboard ----------------
    @app.route("/")
    def home():
        return redirect(url_for("dashboard") if "user" in session else url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        role = session.get("user", {}).get("role")
        tiles = dashboard_tiles_for(role)
        return render_template("dashboard.html", tiles=tiles)

    # ... (many routes omitted for brevity in this editor paste) ...

    # ---------------- end of routes ----------------

    # ---------- AI Chatbot API route ----------
    @app.route('/api/chat', methods=['POST'])
    def chat():
        import traceback
        try:
            # Validate request has JSON content
            if not request.is_json:
                return jsonify({'error': 'Request must be JSON'}), 400
            
            data = request.get_json()
            if data is None:
                return jsonify({'error': 'Invalid JSON data'}), 400
            
            user_message = data.get('message', '').strip()
            if not user_message:
                return jsonify({'error': 'Message is required'}), 400
            
            history = data.get('history', [])
            
            # Get OpenAI API key from environment
            openai_api_key = os.getenv('OPENAI_API_KEY')
            if not openai_api_key:
                return jsonify({'error': 'OpenAI API key not configured. Please set OPENAI_API_KEY environment variable.'}), 500
            
            # Try to import OpenAI library
            try:
                from openai import OpenAI
            except ImportError as ie:
                return jsonify({
                    'error': 'OpenAI library not installed. Please run: pip install openai',
                    'details': str(ie)
                }), 500
            
            # Initialize OpenAI client
            try:
                client = OpenAI(api_key=openai_api_key)
            except Exception as ce:
                return jsonify({
                    'error': 'Failed to initialize OpenAI client',
                    'details': str(ce)
                }), 500
            
            # Create messages for OpenAI
            messages = [
                {"role": "system", "content": "You are a helpful kitchen management assistant. Help users with meal planning, inventory management, dietary restrictions, and kitchen operations."}
            ]
            
            # Add history if valid
            if isinstance(history, list):
                messages.extend(history)
            
            messages.append({"role": "user", "content": user_message})
            
            # Call OpenAI API (chat.completions)
            try:
                resp = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    max_tokens=500,
                    temperature=0.7
                )
            except Exception as api_error:
                return jsonify({
                    'error': 'OpenAI API call failed',
                    'details': str(api_error)
                }), 500
            
            # Properly access the response content
            bot_response = ""
            if resp and resp.choices and len(resp.choices) > 0:
                bot_response = resp.choices[0].message.content or ""
            
            if not bot_response:
                return jsonify({'error': 'Empty response from OpenAI'}), 500
            
            return jsonify({'response': bot_response})
            
        except Exception as e:
            # Include full stack trace for debugging
            return jsonify({
                'error': 'Internal server error',
                'message': str(e),
                'trace': traceback.format_exc()
            }), 500

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
