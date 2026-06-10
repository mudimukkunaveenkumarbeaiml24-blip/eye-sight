"""
╔══════════════════════════════════════════════════════════════╗
║         NEUROVISION AI — COMPLETE PYTHON BACKEND             ║
║         Stack : Flask + SQLite + Claude AI + ReportLab       ║
║         File  : app.py  (single-file, zero config)           ║
╚══════════════════════════════════════════════════════════════╝

QUICK START
───────────
1.  pip install flask flask-cors werkzeug PyJWT httpx reportlab
2.  python app.py
3.  Open http://localhost:5000

ENVIRONMENT VARIABLES (optional)
──────────────────────────────────
GEMINI_API_KEY     → Your Gemini API key for eye analysis
ANTHROPIC_API_KEY  → Fallback Claude AI key
SECRET_KEY         → Flask session secret (auto-generated if absent)
DATABASE_PATH      → SQLite path (default: neurovision.db)
PORT               → Server port (default: 5000)
AI_PROVIDER        → "gemini" or "claude" (default: gemini)
"""

import os, json, base64, uuid, hashlib, hmac, time, io, re, sqlite3, shutil, subprocess
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

# ─── Flask ────────────────────────────────────────────────────
from flask import (
    Flask, request, jsonify, send_file, send_from_directory, g,
    make_response, session
)
from flask_cors import CORS

# ─── Password hashing ─────────────────────────────────────────
from werkzeug.security import generate_password_hash, check_password_hash

# ─── JWT ──────────────────────────────────────────────────────
import jwt as pyjwt

# ─── HTTP client ──────────────────────────────────────────────
import httpx

# ─── PDF generation ───────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# ──────────────────────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────────────────────
SECRET_KEY      = os.environ.get("SECRET_KEY", os.urandom(32).hex())
DATABASE_PATH   = os.environ.get("DATABASE_PATH", "neurovision.db")
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
AI_PROVIDER     = os.environ.get("AI_PROVIDER", "gemini").lower()
PORT            = int(os.environ.get("PORT", 5000))
JWT_ALGORITHM   = "HS256"
JWT_EXPIRY_DAYS = 7
UPLOAD_FOLDER   = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)

FRONTEND_FOLDER = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(
    __name__,
    static_folder=str(FRONTEND_FOLDER),
    static_url_path=""
)
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True, origins=["*"])

# ──────────────────────────────────────────────────────────────
#  DATABASE SCHEMA
# ──────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    password    TEXT NOT NULL,
    age         INTEGER,
    gender      TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT,
    eye_mode      TEXT DEFAULT 'both',
    overall_score INTEGER DEFAULT 0,
    best_acuity   REAL DEFAULT 0,
    best_fraction TEXT DEFAULT '—',
    levels_passed INTEGER DEFAULT 0,
    strain_risk   INTEGER DEFAULT 0,
    dryness_index INTEGER DEFAULT 0,
    blue_light    INTEGER DEFAULT 0,
    grade         TEXT DEFAULT 'Unknown',
    has_eye_image INTEGER DEFAULT 0,
    ai_analysis   TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    acuity         REAL,
    fraction       TEXT,
    level_name     TEXT,
    target_letters TEXT,
    chosen_answer  TEXT,
    passed         INTEGER DEFAULT 0,
    input_method   TEXT DEFAULT 'click',
    FOREIGN KEY (session_id) REFERENCES test_sessions(id)
);

CREATE TABLE IF NOT EXISTS screen_settings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT UNIQUE NOT NULL,
    brightness      INTEGER DEFAULT 45,
    font_size       INTEGER DEFAULT 16,
    blue_filter     INTEGER DEFAULT 65,
    display_zoom    INTEGER DEFAULT 110,
    dark_mode       INTEGER DEFAULT 1,
    contrast        TEXT DEFAULT 'Medium',
    auto_sunset     INTEGER DEFAULT 0,
    reduce_white    INTEGER DEFAULT 1,
    text_spacing    INTEGER DEFAULT 1,
    vision_profile  TEXT DEFAULT 'mild',
    applied_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES test_sessions(id)
);

CREATE TABLE IF NOT EXISTS eye_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    filename    TEXT,
    analysis    TEXT,
    redness     REAL DEFAULT 0,
    dryness     REAL DEFAULT 0,
    anomaly     REAL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES test_sessions(id)
);

CREATE TABLE IF NOT EXISTS settings_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    brightness      INTEGER,
    font_size       INTEGER,
    blue_filter     INTEGER,
    display_zoom    INTEGER,
    dark_mode       INTEGER,
    contrast        TEXT,
    auto_sunset     INTEGER DEFAULT 0,
    reduce_white    INTEGER DEFAULT 1,
    text_spacing    INTEGER DEFAULT 1,
    vision_profile  TEXT DEFAULT 'mild',
    applied_at      TEXT DEFAULT (datetime('now')),
    apply_result    TEXT,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id)
);
"""

# ──────────────────────────────────────────────────────────────
#  DATABASE HELPERS
# ──────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.executescript(SCHEMA)
    print(f"[DB] Initialized → {DATABASE_PATH}")

# ──────────────────────────────────────────────────────────────
#  WINDOWS DISPLAY SETTINGS HELPER
# ──────────────────────────────────────────────────────────────
def apply_windows_settings(payload: dict) -> dict:
    """
    Run the local PowerShell helper to apply display settings on Windows.
    Requires apply_settings.ps1 in the frontend folder.
    """
    script_path = Path(__file__).resolve().parent.parent / "frontend" / "apply_settings.ps1"
    if not script_path.exists():
        return {"applied": False, "reason": "settings helper script not found", "os": "windows"}

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        return {"applied": False, "reason": "PowerShell not available"}

    dark_mode_flag = "-DarkMode:$True" if bool(payload.get("dark_mode", True)) else "-DarkMode:$False"
    auto_sunset_flag = "-AutoSunset:$True" if bool(payload.get("auto_sunset", False)) else "-AutoSunset:$False"

    cmd = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script_path),
        "-Brightness",    str(int(payload.get("brightness",    45))),
        "-FontSize",      str(int(payload.get("font_size",     16))),
        "-BlueFilter",    str(int(payload.get("blue_filter",   65))),
        "-DisplayZoom",   str(int(payload.get("display_zoom", 110))),
        dark_mode_flag,
        auto_sunset_flag,
        "-Contrast",      str(payload.get("contrast", "Medium")),
        "-ReduceWhite",   str(int(payload.get("reduce_white", 1))),
        "-TextSpacing",   str(int(payload.get("text_spacing", 1))),
    ]

    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "applied":    completed.returncode == 0,
            "reason":     "ok" if completed.returncode == 0 else (completed.stderr or completed.stdout or "PowerShell failed"),
            "returncode": completed.returncode,
            "os":         "windows",
        }
    except Exception as exc:
        return {"applied": False, "reason": str(exc), "os": "windows"}


def apply_macos_settings(payload: dict) -> dict:
    """Apply display settings on macOS using osascript / brightness CLI."""
    results = []

    # Dark mode
    dark_mode = bool(payload.get("dark_mode", True))
    dark_script = (
        'tell application "System Events" to tell appearance preferences '
        f'to set dark mode to {"true" if dark_mode else "false"}'
    )
    try:
        r = subprocess.run(["osascript", "-e", dark_script], capture_output=True, text=True, timeout=10)
        results.append({"setting": "dark_mode", "ok": r.returncode == 0})
    except Exception as e:
        results.append({"setting": "dark_mode", "ok": False, "error": str(e)})

    # Brightness (requires brightness CLI: brew install brightness)
    brightness = int(payload.get("brightness", 45))
    bright_val = round(brightness / 100, 2)
    bright_cli = shutil.which("brightness")
    if bright_cli:
        try:
            r = subprocess.run([bright_cli, str(bright_val)], capture_output=True, text=True, timeout=10)
            results.append({"setting": "brightness", "ok": r.returncode == 0})
        except Exception as e:
            results.append({"setting": "brightness", "ok": False, "error": str(e)})
    else:
        results.append({"setting": "brightness", "ok": False, "reason": "brightness CLI not installed (brew install brightness)"})

    all_ok = all(r.get("ok", False) for r in results)
    return {"applied": all_ok, "results": results, "os": "macos"}


def apply_linux_settings(payload: dict) -> dict:
    """Apply display settings on Linux using xrandr / gsettings."""
    results = []

    # Brightness via xrandr
    brightness = int(payload.get("brightness", 45))
    bright_val = round(brightness / 100, 2)
    xrandr = shutil.which("xrandr")
    if xrandr:
        try:
            # Get primary display name
            disp_result = subprocess.run([xrandr, "--query"], capture_output=True, text=True, timeout=10)
            primary = "eDP-1"
            for line in disp_result.stdout.splitlines():
                if " connected" in line:
                    primary = line.split()[0]
                    break
            r = subprocess.run(
                [xrandr, "--output", primary, "--brightness", str(bright_val)],
                capture_output=True, text=True, timeout=10
            )
            results.append({"setting": "brightness", "ok": r.returncode == 0})
        except Exception as e:
            results.append({"setting": "brightness", "ok": False, "error": str(e)})
    else:
        results.append({"setting": "brightness", "ok": False, "reason": "xrandr not found"})

    # Dark mode via gsettings (GNOME)
    dark_mode = bool(payload.get("dark_mode", True))
    gsettings = shutil.which("gsettings")
    if gsettings:
        theme = "prefer-dark" if dark_mode else "prefer-light"
        try:
            r = subprocess.run(
                [gsettings, "set", "org.gnome.desktop.interface", "color-scheme", theme],
                capture_output=True, text=True, timeout=10
            )
            results.append({"setting": "dark_mode", "ok": r.returncode == 0})
        except Exception as e:
            results.append({"setting": "dark_mode", "ok": False, "error": str(e)})
    else:
        results.append({"setting": "dark_mode", "ok": False, "reason": "gsettings not found (non-GNOME desktop?)"})

    all_ok = all(r.get("ok", False) for r in results)
    return {"applied": all_ok, "results": results, "os": "linux"}


def detect_os() -> str:
    import platform
    s = platform.system().lower()
    if s == "windows":   return "windows"
    if s == "darwin":    return "macos"
    return "linux"


def apply_os_settings(payload: dict) -> dict:
    """Detect OS and apply settings accordingly."""
    os_name = detect_os()
    if os_name == "windows":  return apply_windows_settings(payload)
    if os_name == "macos":    return apply_macos_settings(payload)
    return apply_linux_settings(payload)


# ──────────────────────────────────────────────────────────────
#  VISION PROFILE PRESETS
# ──────────────────────────────────────────────────────────────
VISION_PROFILES = {
    "normal": {
        "brightness": 70, "font_size": 16, "blue_filter": 30,
        "display_zoom": 100, "dark_mode": False, "contrast": "Low",
        "auto_sunset": False, "reduce_white": False, "text_spacing": False,
    },
    "mild": {
        "brightness": 44, "font_size": 18, "blue_filter": 60,
        "display_zoom": 125, "dark_mode": True,  "contrast": "Medium",
        "auto_sunset": False, "reduce_white": True,  "text_spacing": True,
    },
    "moderate": {
        "brightness": 35, "font_size": 20, "blue_filter": 75,
        "display_zoom": 140, "dark_mode": True,  "contrast": "High",
        "auto_sunset": True,  "reduce_white": True,  "text_spacing": True,
    },
    "high": {
        "brightness": 25, "font_size": 22, "blue_filter": 90,
        "display_zoom": 160, "dark_mode": True,  "contrast": "Max",
        "auto_sunset": True,  "reduce_white": True,  "text_spacing": True,
    },
}

def recommend_profile_from_score(overall_score: int, best_acuity: float) -> str:
    """Recommend a vision profile based on test results."""
    if overall_score >= 85 and best_acuity >= 1.0:
        return "normal"
    elif overall_score >= 65 and best_acuity >= 0.5:
        return "mild"
    elif overall_score >= 40 and best_acuity >= 0.25:
        return "moderate"
    return "high"

# ──────────────────────────────────────────────────────────────
#  AUTH HELPERS
# ──────────────────────────────────────────────────────────────
def make_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return pyjwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)

def decode_token(token: str):
    try:
        return pyjwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None

def get_token_from_request():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("nvtoken", "")

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        payload = decode_token(token)
        if not payload:
            return jsonify({"error": "Token expired or invalid"}), 401
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (payload["sub"],)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 401
        g.current_user = dict(user)
        return f(*args, **kwargs)
    return wrapper

def optional_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = get_token_from_request()
        g.current_user = None
        if token:
            payload = decode_token(token)
            if payload:
                db = get_db()
                user = db.execute("SELECT * FROM users WHERE id=?", (payload["sub"],)).fetchone()
                if user:
                    g.current_user = dict(user)
        return f(*args, **kwargs)
    return wrapper

# ──────────────────────────────────────────────────────────────
#  UTILITY
# ──────────────────────────────────────────────────────────────
def ok(data=None, **kwargs):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)

def err(msg, code=400):
    return jsonify({"success": False, "error": msg}), code

def new_id():
    return str(uuid.uuid4())

# ──────────────────────────────────────────────────────────────
#  AI — GEMINI EYE ANALYSIS
# ──────────────────────────────────────────────────────────────
async def analyse_eye_with_gemini(base64_image: str, media_type: str = "image/jpeg") -> dict:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    prompt = """You are an expert ophthalmology AI assistant. Analyse this eye image and return ONLY a JSON object (no markdown, no prose) with these exact keys:

{
  "redness_score": <0.0-1.0 float>,
  "dryness_score": <0.0-1.0 float>,
  "anomaly_score": <0.0-1.0 float>,
  "overall_eye_health": <0.0-1.0 float>,
  "findings": [<3 concise observation strings>],
  "recommendations": [<3 concise action strings>],
  "note": "<one sentence clinical disclaimer>"
}

Scoring: redness 0=white sclera 1=severely red; dryness 0=well lubricated 1=severely dry;
anomaly 0=none 1=significant; overall_eye_health 1=perfect 0=critical.
Conservative and accurate. This is a screening tool only."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": base64_image}},
                {"text": prompt},
            ]
        }],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            part.get("text", "")
            for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        )
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)


async def analyse_eye_with_claude(base64_image: str, media_type: str = "image/jpeg") -> dict:
    if not ANTHROPIC_KEY:
        import random
        return {
            "redness_score": round(random.uniform(0.05, 0.35), 2),
            "dryness_score": round(random.uniform(0.05, 0.40), 2),
            "anomaly_score": round(random.uniform(0.00, 0.15), 2),
            "overall_eye_health": round(random.uniform(0.65, 0.98), 2),
            "findings": [
                "Sclera appears generally white — no significant redness detected.",
                "Eyelid margins look normal.",
                "No obvious vascular anomalies in the visible region.",
            ],
            "recommendations": [
                "Stay hydrated (8 glasses of water/day).",
                "Use lubricating eye drops if experiencing dryness.",
                "Reduce screen time and take regular 20-20-20 breaks.",
            ],
            "note": "Simulated analysis — set ANTHROPIC_API_KEY for real AI analysis.",
        }

    prompt = """You are an expert ophthalmology AI assistant. Analyse this eye image and return ONLY a JSON object (no markdown, no prose) with these exact keys:

{
  "redness_score": <0.0-1.0 float>,
  "dryness_score": <0.0-1.0 float>,
  "anomaly_score": <0.0-1.0 float>,
  "overall_eye_health": <0.0-1.0 float>,
  "findings": [<3 concise observation strings>],
  "recommendations": [<3 concise action strings>],
  "note": "<one sentence clinical disclaimer>"
}

Scoring: redness 0=white sclera 1=severely red; dryness 0=well lubricated 1=severely dry;
anomaly 0=none 1=significant; overall_eye_health 1=perfect 0=critical.
Conservative and accurate. This is a screening tool only."""

    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64_image}},
                {"type": "text", "text": prompt},
            ],
        }],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        content = re.sub(r"```json|```", "", content).strip()
        return json.loads(content)


async def analyse_eye_with_ai(base64_image: str, media_type: str = "image/jpeg") -> dict:
    if AI_PROVIDER == "gemini" and GEMINI_KEY:
        return await analyse_eye_with_gemini(base64_image, media_type)
    if ANTHROPIC_KEY:
        return await analyse_eye_with_claude(base64_image, media_type)

    import random
    return {
        "redness_score": round(random.uniform(0.05, 0.35), 2),
        "dryness_score": round(random.uniform(0.05, 0.40), 2),
        "anomaly_score": round(random.uniform(0.00, 0.15), 2),
        "overall_eye_health": round(random.uniform(0.65, 0.98), 2),
        "findings": [
            "Sclera appears generally white — no significant redness detected.",
            "Eyelid margins look normal.",
            "No obvious vascular anomalies in the visible region.",
        ],
        "recommendations": [
            "Stay hydrated (8 glasses of water/day).",
            "Use lubricating eye drops if experiencing dryness.",
            "Reduce screen time and take regular 20-20-20 breaks.",
        ],
        "note": "Simulated analysis — set GEMINI_API_KEY or ANTHROPIC_API_KEY for real AI analysis.",
    }

# ──────────────────────────────────────────────────────────────
#  ROUTES — AUTH
# ──────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    """POST /api/auth/register — { name, email, password, age?, gender? }"""
    data     = request.get_json(silent=True) or {}
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    age      = data.get("age")
    gender   = data.get("gender", "")

    if not name:                        return err("Name is required")
    if not email or "@" not in email:   return err("Valid email is required")
    if len(password) < 6:               return err("Password must be at least 6 characters")

    db = get_db()
    if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        return err("Email already registered", 409)

    uid    = new_id()
    hashed = generate_password_hash(password)
    db.execute(
        "INSERT INTO users (id, name, email, password, age, gender) VALUES (?,?,?,?,?,?)",
        (uid, name, email, hashed, age, gender)
    )
    db.commit()

    token = make_token(uid)
    user  = db.execute(
        "SELECT id, name, email, age, gender, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    resp  = make_response(ok(dict(user), token=token))
    resp.set_cookie("nvtoken", token, httponly=True, samesite="Lax", max_age=JWT_EXPIRY_DAYS * 86400)
    return resp


@app.route("/api/auth/login", methods=["POST"])
def login():
    """POST /api/auth/login — { email, password }"""
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return err("Email and password required")

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not check_password_hash(user["password"], password):
        return err("Invalid email or password", 401)

    token = make_token(user["id"])
    safe  = {k: user[k] for k in ("id", "name", "email", "age", "gender", "created_at")}
    resp  = make_response(ok(safe, token=token))
    resp.set_cookie("nvtoken", token, httponly=True, samesite="Lax", max_age=JWT_EXPIRY_DAYS * 86400)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    resp = make_response(ok({"message": "Logged out"}))
    resp.delete_cookie("nvtoken")
    return resp


@app.route("/api/auth/me", methods=["GET"])
@login_required
def me():
    u = g.current_user
    return ok({k: u[k] for k in ("id", "name", "email", "age", "gender", "created_at")})


@app.route("/api/auth/update", methods=["PUT"])
@login_required
def update_profile():
    """PUT /api/auth/update — update name, age, gender"""
    data   = request.get_json(silent=True) or {}
    uid    = g.current_user["id"]
    db     = get_db()
    fields = {}
    if "name" in data and data["name"]:   fields["name"]   = data["name"].strip()
    if "age" in data:                     fields["age"]    = data["age"]
    if "gender" in data:                  fields["gender"] = data["gender"]
    if not fields:
        return err("Nothing to update")

    set_clause = ", ".join(f"{k}=?" for k in fields)
    db.execute(
        f"UPDATE users SET {set_clause}, updated_at=datetime('now') WHERE id=?",
        (*fields.values(), uid)
    )
    db.commit()
    user = db.execute(
        "SELECT id, name, email, age, gender, created_at FROM users WHERE id=?", (uid,)
    ).fetchone()
    return ok(dict(user))


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    """POST /api/auth/change-password — { current_password, new_password }"""
    data         = request.get_json(silent=True) or {}
    current_pwd  = data.get("current_password", "")
    new_pwd      = data.get("new_password", "")

    if not current_pwd or not new_pwd:
        return err("Both current and new password are required")
    if len(new_pwd) < 6:
        return err("New password must be at least 6 characters")

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (g.current_user["id"],)).fetchone()
    if not check_password_hash(user["password"], current_pwd):
        return err("Current password is incorrect", 401)

    db.execute(
        "UPDATE users SET password=?, updated_at=datetime('now') WHERE id=?",
        (generate_password_hash(new_pwd), g.current_user["id"])
    )
    db.commit()
    return ok({"message": "Password changed successfully"})

# ──────────────────────────────────────────────────────────────
#  ROUTES — TEST SESSIONS
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions", methods=["POST"])
@optional_auth
def create_session():
    """POST /api/sessions — { eye_mode? }"""
    data     = request.get_json(silent=True) or {}
    sid      = new_id()
    uid      = g.current_user["id"] if g.current_user else None
    eye_mode = data.get("eye_mode", "both")

    db = get_db()
    db.execute(
        "INSERT INTO test_sessions (id, user_id, eye_mode) VALUES (?,?,?)",
        (sid, uid, eye_mode)
    )
    db.commit()
    return ok({"session_id": sid, "eye_mode": eye_mode}), 201


@app.route("/api/sessions/<sid>/results", methods=["POST"])
@optional_auth
def save_results(sid: str):
    """
    POST /api/sessions/<sid>/results
    Body: {
        results: [{acuity, fraction, level_name, target_letters, chosen_answer, passed, input_method}],
        overall_score, best_acuity, best_fraction, levels_passed,
        strain_risk, dryness_index, blue_light, grade, eye_mode
    }
    """
    data = request.get_json(silent=True) or {}
    db   = get_db()

    sess = db.execute("SELECT id FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    results = data.get("results", [])

    db.execute("""
        UPDATE test_sessions SET
            eye_mode=?, overall_score=?, best_acuity=?, best_fraction=?,
            levels_passed=?, strain_risk=?, dryness_index=?,
            blue_light=?, grade=?
        WHERE id=?
    """, (
        data.get("eye_mode",      "both"),
        data.get("overall_score",  0),
        data.get("best_acuity",    0),
        data.get("best_fraction", "—"),
        data.get("levels_passed",  0),
        data.get("strain_risk",    0),
        data.get("dryness_index",  0),
        data.get("blue_light",     0),
        data.get("grade",   "Unknown"),
        sid,
    ))

    db.execute("DELETE FROM test_results WHERE session_id=?", (sid,))
    for row in results:
        db.execute("""
            INSERT INTO test_results
                (session_id, acuity, fraction, level_name, target_letters, chosen_answer, passed, input_method)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            sid,
            row.get("acuity"), row.get("fraction"), row.get("level_name"),
            row.get("letters"), row.get("chosen"),
            1 if row.get("passed") else 0,
            row.get("inputMethod", "click"),
        ))

    db.commit()
    return ok({"session_id": sid, "rows_saved": len(results)})


@app.route("/api/sessions/<sid>", methods=["GET"])
@optional_auth
def get_session(sid: str):
    """GET /api/sessions/<sid> — full session with results, settings, image"""
    db   = get_db()
    sess = db.execute("SELECT * FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    rows     = db.execute(
        "SELECT * FROM test_results WHERE session_id=? ORDER BY acuity", (sid,)
    ).fetchall()
    settings = db.execute(
        "SELECT * FROM screen_settings WHERE session_id=?", (sid,)
    ).fetchone()
    image    = db.execute(
        "SELECT * FROM eye_images WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)
    ).fetchone()
    history  = db.execute(
        "SELECT * FROM settings_history WHERE session_id=? ORDER BY applied_at DESC LIMIT 10", (sid,)
    ).fetchall()

    return ok({
        "session":          dict(sess),
        "results":          [dict(r) for r in rows],
        "settings":         dict(settings) if settings else None,
        "image":            dict(image) if image else None,
        "settings_history": [dict(h) for h in history],
    })


@app.route("/api/sessions", methods=["GET"])
@login_required
def list_sessions():
    """GET /api/sessions — all sessions for logged-in user"""
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM test_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (g.current_user["id"],)
    ).fetchall()
    return ok([dict(r) for r in rows])


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@login_required
def delete_session(sid: str):
    """DELETE /api/sessions/<sid>"""
    db = get_db()
    sess = db.execute(
        "SELECT id FROM test_sessions WHERE id=? AND user_id=?",
        (sid, g.current_user["id"])
    ).fetchone()
    if not sess:
        return err("Session not found or not yours", 404)

    db.execute("DELETE FROM test_results    WHERE session_id=?", (sid,))
    db.execute("DELETE FROM screen_settings WHERE session_id=?", (sid,))
    db.execute("DELETE FROM eye_images      WHERE session_id=?", (sid,))
    db.execute("DELETE FROM settings_history WHERE session_id=?", (sid,))
    db.execute("DELETE FROM test_sessions   WHERE id=?",          (sid,))
    db.commit()
    return ok({"deleted": sid})

# ──────────────────────────────────────────────────────────────
#  ROUTES — SCREEN SETTINGS  (FULL FEATURED)
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/settings", methods=["POST"])
@optional_auth
def save_settings(sid: str):
    """
    POST /api/sessions/<sid>/settings

    Body (all optional, sensible defaults):
    {
        brightness      : 10-100   (default 45)
        font_size       : 12-28    (default 16)
        blue_filter     : 0-100    (default 65)
        display_zoom    : 75-200   (default 110)
        dark_mode       : bool     (default true)
        contrast        : Low|Medium|High|Max  (default Medium)
        auto_sunset     : bool     (default false)
        reduce_white    : bool     (default true)
        text_spacing    : bool     (default true)
        vision_profile  : normal|mild|moderate|high  (default mild)
        apply_to_os     : bool     (default true)  — attempt OS-level apply
    }

    Returns:
    {
        session_id, settings, recommended_profile, apply_result, os_detected
    }
    """
    data = request.get_json(silent=True) or {}
    db   = get_db()

    sess = db.execute("SELECT id, overall_score, best_acuity FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    # Auto-recommend profile from test results if not provided
    vision_profile = data.get("vision_profile")
    if not vision_profile:
        vision_profile = recommend_profile_from_score(
            sess["overall_score"] or 0,
            sess["best_acuity"]   or 0.0
        )

    # Merge with profile defaults so any missing keys fall back to the profile
    profile_defaults = VISION_PROFILES.get(vision_profile, VISION_PROFILES["mild"])
    brightness    = int(data.get("brightness",   profile_defaults["brightness"]))
    font_size     = int(data.get("font_size",     profile_defaults["font_size"]))
    blue_filter   = int(data.get("blue_filter",   profile_defaults["blue_filter"]))
    display_zoom  = int(data.get("display_zoom",  profile_defaults["display_zoom"]))
    dark_mode     = bool(data.get("dark_mode",    profile_defaults["dark_mode"]))
    contrast      = str(data.get("contrast",      profile_defaults["contrast"]))
    auto_sunset   = bool(data.get("auto_sunset",  profile_defaults["auto_sunset"]))
    reduce_white  = bool(data.get("reduce_white", profile_defaults["reduce_white"]))
    text_spacing  = bool(data.get("text_spacing", profile_defaults["text_spacing"]))
    apply_to_os   = bool(data.get("apply_to_os",  True))

    # Clamp values
    brightness   = max(10,  min(100, brightness))
    font_size    = max(12,  min(28,  font_size))
    blue_filter  = max(0,   min(100, blue_filter))
    display_zoom = max(75,  min(200, display_zoom))
    if contrast not in ("Low", "Medium", "High", "Max"):
        contrast = "Medium"

    settings_payload = dict(
        brightness=brightness, font_size=font_size, blue_filter=blue_filter,
        display_zoom=display_zoom, dark_mode=dark_mode, contrast=contrast,
        auto_sunset=auto_sunset, reduce_white=reduce_white, text_spacing=text_spacing,
        vision_profile=vision_profile,
    )

    # Upsert screen_settings
    db.execute("""
        INSERT INTO screen_settings
            (session_id, brightness, font_size, blue_filter, display_zoom,
             dark_mode, contrast, auto_sunset, reduce_white, text_spacing, vision_profile, applied_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(session_id) DO UPDATE SET
            brightness=excluded.brightness,
            font_size=excluded.font_size,
            blue_filter=excluded.blue_filter,
            display_zoom=excluded.display_zoom,
            dark_mode=excluded.dark_mode,
            contrast=excluded.contrast,
            auto_sunset=excluded.auto_sunset,
            reduce_white=excluded.reduce_white,
            text_spacing=excluded.text_spacing,
            vision_profile=excluded.vision_profile,
            applied_at=datetime('now')
    """, (
        sid, brightness, font_size, blue_filter, display_zoom,
        1 if dark_mode else 0, contrast,
        1 if auto_sunset else 0, 1 if reduce_white else 0,
        1 if text_spacing else 0, vision_profile,
    ))

    # Apply to OS
    apply_result = {"applied": False, "reason": "apply_to_os=false"}
    if apply_to_os:
        apply_result = apply_os_settings(settings_payload)

    # Log to history
    db.execute("""
        INSERT INTO settings_history
            (session_id, brightness, font_size, blue_filter, display_zoom,
             dark_mode, contrast, auto_sunset, reduce_white, text_spacing,
             vision_profile, applied_at, apply_result)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?)
    """, (
        sid, brightness, font_size, blue_filter, display_zoom,
        1 if dark_mode else 0, contrast,
        1 if auto_sunset else 0, 1 if reduce_white else 0,
        1 if text_spacing else 0, vision_profile,
        json.dumps(apply_result),
    ))

    db.commit()

    return ok({
        "session_id":          sid,
        "settings":            settings_payload,
        "recommended_profile": vision_profile,
        "apply_result":        apply_result,
        "os_detected":         detect_os(),
    })


@app.route("/api/sessions/<sid>/settings", methods=["GET"])
@optional_auth
def get_settings(sid: str):
    """GET /api/sessions/<sid>/settings — current settings + history"""
    db   = get_db()
    sess = db.execute("SELECT id FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    settings = db.execute(
        "SELECT * FROM screen_settings WHERE session_id=?", (sid,)
    ).fetchone()
    history  = db.execute(
        "SELECT * FROM settings_history WHERE session_id=? ORDER BY applied_at DESC LIMIT 20", (sid,)
    ).fetchall()

    return ok({
        "settings": dict(settings) if settings else None,
        "history":  [dict(h) for h in history],
        "profiles": VISION_PROFILES,
    })


@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    """GET /api/profiles — all vision profile presets"""
    return ok(VISION_PROFILES)


@app.route("/api/sessions/<sid>/settings/recommend", methods=["GET"])
@optional_auth
def recommend_settings(sid: str):
    """
    GET /api/sessions/<sid>/settings/recommend
    Returns AI-recommended settings based on test results.
    """
    db   = get_db()
    sess = db.execute("SELECT * FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    sess           = dict(sess)
    profile_name   = recommend_profile_from_score(
        sess.get("overall_score", 0), sess.get("best_acuity", 0.0)
    )
    profile        = VISION_PROFILES[profile_name].copy()
    profile["vision_profile"] = profile_name

    # Fine-tune from session metrics
    strain_risk   = sess.get("strain_risk",   0)
    dryness_index = sess.get("dryness_index", 0)
    blue_light    = sess.get("blue_light",    0)

    if strain_risk > 70:
        profile["brightness"] = max(10, profile["brightness"] - 10)
    if dryness_index > 60:
        profile["blue_filter"] = min(100, profile["blue_filter"] + 10)
    if blue_light > 80:
        profile["blue_filter"] = min(100, profile["blue_filter"] + 5)

    reasoning = []
    if sess.get("best_acuity", 1.0) < 0.5:
        reasoning.append("Visual acuity below 20/40 — larger font and higher zoom recommended.")
    if strain_risk > 70:
        reasoning.append("High eye strain risk detected — reduced brightness applied.")
    if dryness_index > 60:
        reasoning.append("Elevated dryness index — stronger blue light filter applied.")
    if sess.get("overall_score", 100) < 50:
        reasoning.append("Low overall score — maximum accessibility settings recommended.")

    return ok({
        "session_id":   sid,
        "profile_name": profile_name,
        "settings":     profile,
        "reasoning":    reasoning,
        "test_summary": {
            "overall_score":  sess.get("overall_score"),
            "best_acuity":    sess.get("best_acuity"),
            "best_fraction":  sess.get("best_fraction"),
            "levels_passed":  sess.get("levels_passed"),
            "strain_risk":    strain_risk,
            "dryness_index":  dryness_index,
            "blue_light":     blue_light,
            "grade":          sess.get("grade"),
        },
    })


@app.route("/api/sessions/<sid>/settings/apply-profile", methods=["POST"])
@optional_auth
def apply_profile(sid: str):
    """
    POST /api/sessions/<sid>/settings/apply-profile
    Body: { profile: "normal"|"mild"|"moderate"|"high", apply_to_os?: bool }
    Instantly apply a preset profile.
    """
    data    = request.get_json(silent=True) or {}
    profile = data.get("profile", "mild")
    if profile not in VISION_PROFILES:
        return err(f"Unknown profile '{profile}'. Valid: {list(VISION_PROFILES.keys())}")

    payload = VISION_PROFILES[profile].copy()
    payload["vision_profile"] = profile
    payload["apply_to_os"]    = bool(data.get("apply_to_os", True))

    # Reuse the save_settings logic
    request._cached_json = (payload, payload)  # patch for internal reuse
    with app.test_request_context(
        f"/api/sessions/{sid}/settings",
        method="POST",
        json=payload,
        headers={"Authorization": request.headers.get("Authorization", "")},
    ):
        return save_settings(sid)

# ──────────────────────────────────────────────────────────────
#  ROUTES — EYE IMAGE UPLOAD + AI ANALYSIS
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/eye-image", methods=["POST"])
@optional_auth
def upload_eye_image(sid: str):
    """
    POST /api/sessions/<sid>/eye-image
    Accepts JSON { image_base64: "data:image/...;base64,..." }
    OR multipart file field "image".
    Saves image, runs AI analysis, returns structured findings.
    """
    db = get_db()

    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("image")
        if not f:
            return err("No image file provided")
        raw   = f.read()
        b64   = base64.b64encode(raw).decode()
        mime  = f.mimetype or "image/jpeg"
        ext   = mime.split("/")[-1]
        fname = f"{sid}_{int(time.time())}.{ext}"
        fpath = UPLOAD_FOLDER / fname
        fpath.write_bytes(raw)
    else:
        data  = request.get_json(silent=True) or {}
        b64_d = data.get("image_base64", "")
        if not b64_d:
            return err("No image_base64 provided")
        if "," in b64_d:
            header, b64 = b64_d.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
        else:
            b64  = b64_d
            mime = "image/jpeg"
        ext   = mime.split("/")[-1]
        fname = f"{sid}_{int(time.time())}.{ext}"
        fpath = UPLOAD_FOLDER / fname
        fpath.write_bytes(base64.b64decode(b64))

    import asyncio
    try:
        loop     = asyncio.new_event_loop()
        analysis = loop.run_until_complete(analyse_eye_with_ai(b64, mime))
    except Exception as ex:
        analysis = {
            "redness_score": 0.1, "dryness_score": 0.1, "anomaly_score": 0.05,
            "overall_eye_health": 0.85,
            "findings": ["Analysis unavailable — check your API key or network."],
            "recommendations": ["Consult a licensed ophthalmologist for a proper examination."],
            "note": str(ex),
        }
    finally:
        loop.close()

    analysis_json = json.dumps(analysis)

    db.execute("""
        INSERT INTO eye_images (session_id, filename, analysis, redness, dryness, anomaly)
        VALUES (?,?,?,?,?,?)
    """, (
        sid, fname, analysis_json,
        analysis.get("redness_score", 0),
        analysis.get("dryness_score", 0),
        analysis.get("anomaly_score", 0),
    ))
    db.execute(
        "UPDATE test_sessions SET has_eye_image=1, ai_analysis=? WHERE id=?",
        (analysis_json, sid)
    )
    db.commit()

    return ok({"session_id": sid, "filename": fname, "analysis": analysis})

# ──────────────────────────────────────────────────────────────
#  ROUTES — PDF REPORT
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/report.pdf", methods=["GET"])
@optional_auth
def download_pdf(sid: str):
    """GET /api/sessions/<sid>/report.pdf"""
    db   = get_db()
    sess = db.execute("SELECT * FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    rows     = db.execute(
        "SELECT * FROM test_results WHERE session_id=? ORDER BY acuity", (sid,)
    ).fetchall()
    settings = db.execute(
        "SELECT * FROM screen_settings WHERE session_id=?", (sid,)
    ).fetchone()

    sess     = dict(sess)
    rows     = [dict(r) for r in rows]
    settings = dict(settings) if settings else {}

    user_name = "Guest"
    if sess.get("user_id"):
        u = db.execute(
            "SELECT name, age, gender FROM users WHERE id=?", (sess["user_id"],)
        ).fetchone()
        if u:
            user_name = f"{u['name']}  ·  Age {u['age']}  ·  {u['gender']}"

    pdf_bytes = build_pdf(sess, rows, settings, user_name)
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    return send_file(
        buf, mimetype="application/pdf", as_attachment=True,
        download_name=f"NeuroVision_Report_{sid[:8]}.pdf",
    )


def build_pdf(sess: dict, rows: list, settings: dict, user_name: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm,  bottomMargin=18*mm,
    )

    NAVY  = colors.HexColor("#0a1228")
    TEAL  = colors.HexColor("#0ff4c6")
    BLUE  = colors.HexColor("#3b82f6")
    RED   = colors.HexColor("#ef4444")
    GREEN = colors.HexColor("#22c55e")
    GRAY  = colors.HexColor("#64748b")
    WHITE = colors.white

    h1  = ParagraphStyle("H1",  fontName="Helvetica-Bold", fontSize=22, textColor=NAVY,  spaceAfter=4)
    h2  = ParagraphStyle("H2",  fontName="Helvetica-Bold", fontSize=13, textColor=BLUE,  spaceBefore=10, spaceAfter=4)
    bod = ParagraphStyle("Bod", fontName="Helvetica",       fontSize=10, textColor=NAVY,  spaceAfter=4, leading=14)
    sml = ParagraphStyle("Sml", fontName="Helvetica",       fontSize=8,  textColor=GRAY,  spaceAfter=2)

    story = []

    # Header
    story.append(Paragraph("NeuroVision AI", h1))
    story.append(Paragraph("Vision Health Report",
        ParagraphStyle("", fontName="Helvetica-Bold", fontSize=15, textColor=BLUE, spaceAfter=2)))
    story.append(HRFlowable(width="100%", thickness=2, color=TEAL, spaceAfter=8))

    # Patient Info
    story.append(Paragraph("Patient Information", h2))
    info_data = [
        ["Patient",    user_name],
        ["Session ID", sess["id"]],
        ["Date",       sess.get("created_at", "—")[:16]],
        ["Eye Mode",   sess.get("eye_mode", "both").capitalize()],
    ]
    info_tbl = Table(info_data, colWidths=[40*mm, 130*mm])
    info_tbl.setStyle(TableStyle([
        ("FONTNAME",        (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",        (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",       (0, 0), (0, -1),  GRAY),
        ("TEXTCOLOR",       (1, 0), (1, -1),  NAVY),
        ("FONTNAME",        (1, 0), (1, -1),  "Helvetica-Bold"),
        ("ROWBACKGROUNDS",  (0, 0), (-1, -1), [colors.HexColor("#f8fafc"), WHITE]),
        ("BOTTOMPADDING",   (0, 0), (-1, -1), 5),
        ("TOPPADDING",      (0, 0), (-1, -1), 5),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 8))

    # Scores
    story.append(Paragraph("Overall Score", h2))
    grade  = sess.get("grade",         "Unknown")
    score  = sess.get("overall_score",  0)
    passed = sess.get("levels_passed",  0)
    best_a = sess.get("best_acuity",    0)
    best_f = sess.get("best_fraction", "—")

    score_color = GREEN if score >= 80 else (BLUE if score >= 60 else RED)
    summary_data = [
        [f"Overall Score: {score}/100", f"Grade: {grade}"],
        [f"Levels Passed: {passed}/11",  f"Best Acuity: {best_a} ({best_f})"],
        [f"Eye Strain Risk: {sess.get('strain_risk', 0)}%", f"Blue Light: {sess.get('blue_light', 0)}%"],
    ]
    s_tbl = Table(summary_data, colWidths=[85*mm, 85*mm])
    s_tbl.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TEXTCOLOR",     (0, 0), (0,  0),  score_color),
        ("TEXTCOLOR",     (0, 1), (-1, -1), NAVY),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#f0f9ff")),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 10))

    # Snellen Results
    story.append(Paragraph("Snellen Chart Results — All 11 Levels", h2))
    header = ["Acuity", "Fraction", "Level", "Target", "Answer", "Result"]
    data   = [header]
    for r in rows:
        data.append([
            str(r.get("acuity",         "—")),
            str(r.get("fraction",        "—")),
            str(r.get("level_name",      "—")),
            str(r.get("target_letters",  "—")),
            str(r.get("chosen_answer",   "—")),
            "✓ PASS" if r.get("passed") else "✗ FAIL",
        ])

    col_w = [22*mm, 22*mm, 38*mm, 30*mm, 25*mm, 18*mm]
    tbl   = Table(data, colWidths=col_w, repeatRows=1)
    tbl_style = [
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#f8fafc"), WHITE]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(rows, start=1):
        c = GREEN if r.get("passed") else RED
        tbl_style.append(("TEXTCOLOR", (5, i), (5, i), c))
        tbl_style.append(("FONTNAME",  (5, i), (5, i), "Helvetica-Bold"))
    tbl.setStyle(TableStyle(tbl_style))
    story.append(tbl)
    story.append(Spacer(1, 10))

    # Screen Settings
    if settings:
        story.append(Paragraph("Recommended Screen Settings", h2))
        sdata = [
            ["Brightness",   f"{settings.get('brightness',   45)}%",
             "Font Size",    f"{settings.get('font_size',    16)}px"],
            ["Blue Filter",  f"{settings.get('blue_filter',  65)}%",
             "Display Zoom", f"{settings.get('display_zoom',110)}%"],
            ["Dark Mode",    "Yes" if settings.get("dark_mode") else "No",
             "Contrast",     str(settings.get("contrast", "Medium"))],
            ["Auto Sunset",  "Yes" if settings.get("auto_sunset") else "No",
             "Text Spacing", "Yes" if settings.get("text_spacing") else "No"],
            ["Profile",      str(settings.get("vision_profile", "mild")),
             "Applied At",   str(settings.get("applied_at", "—"))[:16]],
        ]
        st = Table(sdata, colWidths=[35*mm, 30*mm, 35*mm, 30*mm])
        st.setStyle(TableStyle([
            ("FONTNAME",       (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE",       (0, 0), (-1, -1), 9),
            ("TEXTCOLOR",      (0, 0), (0, -1),  GRAY),
            ("TEXTCOLOR",      (2, 0), (2, -1),  GRAY),
            ("FONTNAME",       (1, 0), (1, -1),  "Helvetica-Bold"),
            ("FONTNAME",       (3, 0), (3, -1),  "Helvetica-Bold"),
            ("TEXTCOLOR",      (1, 0), (1, -1),  BLUE),
            ("TEXTCOLOR",      (3, 0), (3, -1),  BLUE),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f8fafc"), WHITE]),
            ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",     (0, 0), (-1, -1), 6),
            ("LEFTPADDING",    (0, 0), (-1, -1), 8),
        ]))
        story.append(st)
        story.append(Spacer(1, 10))

    # Recommendations
    story.append(Paragraph("Personalized Recommendations", h2))
    recs = [
        "Apply the 20-20-20 rule: every 20 minutes, look 20 feet away for 20 seconds.",
        f"Enable blue light filter at {sess.get('blue_light', 65)}% on all digital screens.",
        "Maintain 50–70 cm screen distance for optimal viewing comfort.",
        "Stay well-hydrated — dehydration directly affects eye lubrication.",
        "Schedule an annual comprehensive eye exam with a licensed optometrist.",
    ]
    if best_a < 1.0:
        recs.insert(0, "⚠  Vision below 20/20 — consult an optometrist for corrective lenses.")
    for rec in recs:
        story.append(Paragraph(f"• {rec}", bod))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
    story.append(Paragraph(
        "Generated by NeuroVision AI  ·  This report is for informational screening purposes only. "
        "It is not a medical diagnosis. Please consult a licensed ophthalmologist for professional evaluation.",
        sml
    ))

    doc.build(story)
    return buf.getvalue()

# ──────────────────────────────────────────────────────────────
#  ROUTES — TEXT REPORT
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/report.txt", methods=["GET"])
@optional_auth
def download_txt(sid: str):
    """GET /api/sessions/<sid>/report.txt"""
    db   = get_db()
    sess = db.execute("SELECT * FROM test_sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        return err("Session not found", 404)

    rows     = db.execute(
        "SELECT * FROM test_results WHERE session_id=? ORDER BY acuity", (sid,)
    ).fetchall()
    settings = db.execute(
        "SELECT * FROM screen_settings WHERE session_id=?", (sid,)
    ).fetchone()

    sess     = dict(sess)
    rows     = [dict(r) for r in rows]
    settings = dict(settings) if settings else {}

    user_name = "Guest"
    if sess.get("user_id"):
        u = db.execute("SELECT name FROM users WHERE id=?", (sess["user_id"],)).fetchone()
        if u:
            user_name = u["name"]

    lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║         NEUROVISION AI — COMPLETE VISION HEALTH REPORT       ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        "PATIENT INFORMATION",
        "─" * 40,
        f"  Patient      : {user_name}",
        f"  Session ID   : {sess['id']}",
        f"  Date         : {sess.get('created_at', '—')[:16]}",
        f"  Eye Mode     : {sess.get('eye_mode', 'both')}",
        "",
        f"OVERALL SCORE  :  {sess.get('overall_score', 0)} / 100  ({sess.get('grade', '—')})",
        "",
        "SNELLEN CHART RESULTS",
        "─" * 70,
        f"  {'Acuity':<8} {'Fraction':<10} {'Level':<22} {'Target':<22} {'Answer':<12} Result",
        f"  {'─'*7} {'─'*9} {'─'*21} {'─'*21} {'─'*11} {'─'*6}",
    ]
    for r in rows:
        lines.append(
            f"  {str(r.get('acuity', '')):<8} {str(r.get('fraction', '')):<10} "
            f"{str(r.get('level_name', '')):<22} {str(r.get('target_letters', '')):<22} "
            f"{str(r.get('chosen_answer', '')):<12} {'PASS' if r.get('passed') else 'FAIL'}"
        )

    if settings:
        lines += [
            "",
            "SCREEN SETTINGS APPLIED",
            "─" * 40,
            f"  Vision Profile : {settings.get('vision_profile', 'mild')}",
            f"  Brightness     : {settings.get('brightness', 45)}%",
            f"  Font Size      : {settings.get('font_size', 16)}px",
            f"  Blue Filter    : {settings.get('blue_filter', 65)}%",
            f"  Display Zoom   : {settings.get('display_zoom', 110)}%",
            f"  Dark Mode      : {'Yes' if settings.get('dark_mode') else 'No'}",
            f"  Contrast       : {settings.get('contrast', 'Medium')}",
            f"  Text Spacing   : {'Yes' if settings.get('text_spacing') else 'No'}",
            f"  Applied At     : {settings.get('applied_at', '—')[:16]}",
        ]

    lines += [
        "",
        "SUMMARY",
        "─" * 40,
        f"  Levels Passed  : {sess.get('levels_passed', 0)} / 11",
        f"  Best Acuity    : {sess.get('best_acuity', 0)} ({sess.get('best_fraction', '—')})",
        f"  Eye Strain Risk: {sess.get('strain_risk', 0)}%",
        f"  Blue Light     : {sess.get('blue_light', 0)}%",
        "",
        "─" * 70,
        "Generated by NeuroVision AI · For informational purposes only.",
        "─" * 70,
    ]

    text = "\n".join(lines)
    return send_file(
        io.BytesIO(text.encode()), mimetype="text/plain",
        as_attachment=True, download_name=f"NeuroVision_Report_{sid[:8]}.txt",
    )

# ──────────────────────────────────────────────────────────────
#  ROUTES — STATISTICS
# ──────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
@login_required
def user_stats():
    """GET /api/stats — historical trend for logged-in user"""
    uid = g.current_user["id"]
    db  = get_db()

    sessions = db.execute("""
        SELECT id, overall_score, best_acuity, levels_passed, grade, created_at
        FROM test_sessions
        WHERE user_id=?
        ORDER BY created_at ASC
        LIMIT 20
    """, (uid,)).fetchall()

    best = db.execute("""
        SELECT MAX(best_acuity)    AS best_acuity,
               MAX(overall_score)  AS best_score,
               MAX(levels_passed)  AS most_passed
        FROM test_sessions WHERE user_id=?
    """, (uid,)).fetchone()

    return ok({
        "trend":          [dict(s) for s in sessions],
        "best":           dict(best) if best else {},
        "total_sessions": len(sessions),
    })


@app.route("/api/stats/global", methods=["GET"])
def global_stats():
    """GET /api/stats/global — anonymous aggregate stats"""
    db  = get_db()
    row = db.execute("""
        SELECT
            COUNT(*)          AS total_sessions,
            AVG(overall_score) AS avg_score,
            AVG(levels_passed) AS avg_passed,
            MAX(best_acuity)   AS top_acuity
        FROM test_sessions
    """).fetchone()
    return ok(dict(row))

# ──────────────────────────────────────────────────────────────
#  ROUTES — HEALTH CHECK
# ──────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return ok({
        "status":      "ok",
        "version":     "2.0.0",
        "database":    DATABASE_PATH,
        "ai_provider": AI_PROVIDER,
        "ai_enabled":  bool(GEMINI_KEY or ANTHROPIC_KEY),
        "os_detected": detect_os(),
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    })

# ──────────────────────────────────────────────────────────────
#  STATIC / FRONTEND
# ──────────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and (FRONTEND_FOLDER / path).exists():
        return send_from_directory(str(FRONTEND_FOLDER), path)
    return send_from_directory(str(FRONTEND_FOLDER), "index.html")

# ──────────────────────────────────────────────────────────────
#  ERROR HANDLERS
# ──────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(_):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"success": False, "error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"success": False, "error": "Internal server error", "detail": str(e)}), 500

# ──────────────────────────────────────────────────────────────
#  ENTRYPOINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("""
╔══════════════════════════════════════════════════════════════╗
║         NEUROVISION AI  v2.0  —  BACKEND SERVER              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║   AUTH                                                       ║
║     POST   /api/auth/register                                ║
║     POST   /api/auth/login                                   ║
║     POST   /api/auth/logout                                  ║
║     GET    /api/auth/me                                      ║
║     PUT    /api/auth/update                                  ║
║     POST   /api/auth/change-password                         ║
║                                                              ║
║   TEST SESSIONS                                              ║
║     POST   /api/sessions                                     ║
║     GET    /api/sessions                (auth)               ║
║     GET    /api/sessions/<id>                                ║
║     POST   /api/sessions/<id>/results                        ║
║     DELETE /api/sessions/<id>           (auth)               ║
║                                                              ║
║   SCREEN SETTINGS  ★ FULL FEATURED ★                        ║
║     POST   /api/sessions/<id>/settings  → save + apply OS   ║
║     GET    /api/sessions/<id>/settings  → current + history  ║
║     GET    /api/sessions/<id>/settings/recommend             ║
║     POST   /api/sessions/<id>/settings/apply-profile         ║
║     GET    /api/profiles                → all presets        ║
║                                                              ║
║   EYE IMAGE & REPORT                                         ║
║     POST   /api/sessions/<id>/eye-image                      ║
║     GET    /api/sessions/<id>/report.pdf                     ║
║     GET    /api/sessions/<id>/report.txt                     ║
║                                                              ║
║   STATS                                                      ║
║     GET    /api/stats                   (auth)               ║
║     GET    /api/stats/global                                 ║
║                                                              ║
║   HEALTH                                                     ║
║     GET    /api/health                                       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=PORT, debug=True)
