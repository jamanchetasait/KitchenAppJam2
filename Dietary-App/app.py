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
