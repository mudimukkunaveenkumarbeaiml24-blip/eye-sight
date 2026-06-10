"""
╔══════════════════════════════════════════════════════════════╗
║     NEUROVISION AI — VERCEL-COMPATIBLE FLASK BACKEND         ║
║     Stack : Flask + In-Memory Store + Claude/Gemini AI       ║
║     Vercel Limitations Handled:                              ║
║       ✓ No SQLite (serverless = no persistent filesystem)    ║
║       ✓ No subprocess / PowerShell calls                     ║
║       ✓ No asyncio (sync httpx only)                         ║
║       ✓ No file uploads to disk                              ║
║       ✓ ReportLab PDF in-memory only                         ║
╚══════════════════════════════════════════════════════════════╝

QUICK START (local)
───────────────────
1. pip install -r requirements.txt
2. python app.py

DEPLOY TO VERCEL
────────────────
1. Push this folder to GitHub
2. Import repo on vercel.com
3. Set environment variables in Vercel dashboard:
     ANTHROPIC_API_KEY  or  GEMINI_API_KEY
     SECRET_KEY  (any random string)

ENVIRONMENT VARIABLES
──────────────────────
GEMINI_API_KEY     → Gemini 1.5 Flash for eye analysis
ANTHROPIC_API_KEY  → Claude Sonnet fallback
SECRET_KEY         → JWT secret (auto-generated if absent)
AI_PROVIDER        → "gemini" or "claude" (default: gemini)
PORT               → Local port (default: 5000)
"""

import os, json, base64, uuid, time, io, re
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, send_file, make_response, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt as pyjwt
import httpx

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.styles import ParagraphStyle

# ──────────────────────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────────────────────
SECRET_KEY      = os.environ.get("SECRET_KEY", "neurovision-dev-secret-change-in-prod")
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
AI_PROVIDER     = os.environ.get("AI_PROVIDER", "gemini").lower()
PORT            = int(os.environ.get("PORT", 5000))
JWT_ALGORITHM   = "HS256"
JWT_EXPIRY_DAYS = 7

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True, origins=["*"])

# ──────────────────────────────────────────────────────────────
#  IN-MEMORY STORE  (replaces SQLite for Vercel serverless)
#  NOTE: Data resets on each cold start / new serverless instance.
#  For production persistence, swap these dicts with a hosted DB
#  such as Supabase, PlanetScale, or MongoDB Atlas (all free tier).
# ──────────────────────────────────────────────────────────────
USERS            = {}   # uid  -> user dict
EMAIL_INDEX      = {}   # email -> uid
SESSIONS         = {}   # sid  -> session dict
TEST_RESULTS     = {}   # sid  -> list of result dicts
SCREEN_SETTINGS  = {}   # sid  -> settings dict
SETTINGS_HISTORY = {}   # sid  -> list of history dicts
EYE_IMAGES       = {}   # sid  -> list of image+analysis dicts

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
        "display_zoom": 125, "dark_mode": True, "contrast": "Medium",
        "auto_sunset": False, "reduce_white": True, "text_spacing": True,
    },
    "moderate": {
        "brightness": 35, "font_size": 20, "blue_filter": 75,
        "display_zoom": 140, "dark_mode": True, "contrast": "High",
        "auto_sunset": True, "reduce_white": True, "text_spacing": True,
    },
    "high": {
        "brightness": 25, "font_size": 22, "blue_filter": 90,
        "display_zoom": 160, "dark_mode": True, "contrast": "Max",
        "auto_sunset": True, "reduce_white": True, "text_spacing": True,
    },
}

def recommend_profile_from_score(overall_score: int, best_acuity: float) -> str:
    if overall_score >= 85 and best_acuity >= 1.0:   return "normal"
    if overall_score >= 65 and best_acuity >= 0.5:   return "mild"
    if overall_score >= 40 and best_acuity >= 0.25:  return "moderate"
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
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
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
        user = USERS.get(payload["sub"])
        if not user:
            return jsonify({"error": "User not found"}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return wrapper

def optional_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        g.current_user = None
        token = get_token_from_request()
        if token:
            payload = decode_token(token)
            if payload:
                g.current_user = USERS.get(payload["sub"])
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

def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def safe_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "name", "email", "age", "gender", "created_at")}

# ──────────────────────────────────────────────────────────────
#  AI — EYE ANALYSIS  (sync httpx — works on Vercel)
# ──────────────────────────────────────────────────────────────
_EYE_PROMPT = """You are an expert ophthalmology AI assistant. Analyse this eye image and return ONLY a JSON object (no markdown, no prose) with these exact keys:

{
  "redness_score": <0.0-1.0 float>,
  "dryness_score": <0.0-1.0 float>,
  "anomaly_score": <0.0-1.0 float>,
  "overall_eye_health": <0.0-1.0 float>,
  "findings": [<3 concise observation strings>],
  "recommendations": [<3 concise action strings>],
  "note": "<one sentence clinical disclaimer>"
}

Scoring: redness 0=white sclera 1=severely red; dryness 0=lubricated 1=severely dry;
anomaly 0=none 1=significant; overall_eye_health 1=perfect 0=critical.
Be conservative. Screening tool only — not a diagnosis."""

def _simulated_analysis(note: str = "") -> dict:
    import random
    return {
        "redness_score":     round(random.uniform(0.05, 0.35), 2),
        "dryness_score":     round(random.uniform(0.05, 0.40), 2),
        "anomaly_score":     round(random.uniform(0.00, 0.15), 2),
        "overall_eye_health":round(random.uniform(0.65, 0.98), 2),
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
        "note": note or "Simulated analysis — set GEMINI_API_KEY or ANTHROPIC_API_KEY for real AI.",
    }

def analyse_eye_with_gemini(b64: str, mime: str = "image/jpeg") -> dict:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": b64}},
            {"text": _EYE_PROMPT},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        text = "".join(
            p.get("text", "")
            for p in resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        )
        return json.loads(re.sub(r"```json|```", "", text).strip())

def analyse_eye_with_claude(b64: str, mime: str = "image/jpeg") -> dict:
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": _EYE_PROMPT},
        ]}],
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        return json.loads(re.sub(r"```json|```", "", text).strip())

def analyse_eye(b64: str, mime: str = "image/jpeg") -> dict:
    try:
        if AI_PROVIDER == "gemini" and GEMINI_KEY:
            return analyse_eye_with_gemini(b64, mime)
        if ANTHROPIC_KEY:
            return analyse_eye_with_claude(b64, mime)
        return _simulated_analysis()
    except Exception as e:
        return _simulated_analysis(note=f"Analysis error: {e}")

# ──────────────────────────────────────────────────────────────
#  ROUTES — AUTH
# ──────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data     = request.get_json(silent=True) or {}
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    age      = data.get("age")
    gender   = data.get("gender", "")

    if not name:                       return err("Name is required")
    if not email or "@" not in email:  return err("Valid email is required")
    if len(password) < 6:              return err("Password must be at least 6 characters")
    if email in EMAIL_INDEX:           return err("Email already registered", 409)

    uid = new_id()
    user = {
        "id": uid, "name": name, "email": email,
        "password": generate_password_hash(password),
        "age": age, "gender": gender, "created_at": now_str(),
    }
    USERS[uid]        = user
    EMAIL_INDEX[email] = uid

    token = make_token(uid)
    resp  = make_response(ok(safe_user(user), token=token))
    resp.set_cookie("nvtoken", token, httponly=True, samesite="Lax", max_age=JWT_EXPIRY_DAYS * 86400)
    return resp


@app.route("/api/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return err("Email and password required")

    uid  = EMAIL_INDEX.get(email)
    user = USERS.get(uid) if uid else None
    if not user or not check_password_hash(user["password"], password):
        return err("Invalid email or password", 401)

    token = make_token(uid)
    resp  = make_response(ok(safe_user(user), token=token))
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
    return ok(safe_user(g.current_user))


@app.route("/api/auth/update", methods=["PUT"])
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    user = g.current_user
    if "name"   in data and data["name"]:  user["name"]   = data["name"].strip()
    if "age"    in data:                   user["age"]    = data["age"]
    if "gender" in data:                   user["gender"] = data["gender"]
    return ok(safe_user(user))


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    data    = request.get_json(silent=True) or {}
    cur_pwd = data.get("current_password", "")
    new_pwd = data.get("new_password", "")
    if not cur_pwd or not new_pwd:   return err("Both passwords are required")
    if len(new_pwd) < 6:             return err("New password must be at least 6 characters")

    user = g.current_user
    if not check_password_hash(user["password"], cur_pwd):
        return err("Current password is incorrect", 401)

    user["password"] = generate_password_hash(new_pwd)
    return ok({"message": "Password changed successfully"})

# ──────────────────────────────────────────────────────────────
#  ROUTES — TEST SESSIONS
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions", methods=["POST"])
@optional_auth
def create_session():
    data     = request.get_json(silent=True) or {}
    sid      = new_id()
    uid      = g.current_user["id"] if g.current_user else None
    eye_mode = data.get("eye_mode", "both")

    SESSIONS[sid] = {
        "id": sid, "user_id": uid, "eye_mode": eye_mode,
        "overall_score": 0, "best_acuity": 0, "best_fraction": "—",
        "levels_passed": 0, "strain_risk": 0, "dryness_index": 0,
        "blue_light": 0, "grade": "Unknown", "has_eye_image": 0,
        "ai_analysis": None, "created_at": now_str(),
    }
    TEST_RESULTS[sid]    = []
    SETTINGS_HISTORY[sid] = []
    EYE_IMAGES[sid]       = []

    return ok({"session_id": sid, "eye_mode": eye_mode}), 201


@app.route("/api/sessions/<sid>/results", methods=["POST"])
@optional_auth
def save_results(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    data    = request.get_json(silent=True) or {}
    sess    = SESSIONS[sid]
    results = data.get("results", [])

    sess.update({
        "eye_mode":      data.get("eye_mode",      "both"),
        "overall_score": data.get("overall_score",  0),
        "best_acuity":   data.get("best_acuity",    0),
        "best_fraction": data.get("best_fraction", "—"),
        "levels_passed": data.get("levels_passed",  0),
        "strain_risk":   data.get("strain_risk",    0),
        "dryness_index": data.get("dryness_index",  0),
        "blue_light":    data.get("blue_light",     0),
        "grade":         data.get("grade",   "Unknown"),
    })

    TEST_RESULTS[sid] = [
        {
            "acuity":         r.get("acuity"),
            "fraction":       r.get("fraction"),
            "level_name":     r.get("level_name"),
            "target_letters": r.get("letters"),
            "chosen_answer":  r.get("chosen"),
            "passed":         1 if r.get("passed") else 0,
            "input_method":   r.get("inputMethod", "click"),
        }
        for r in results
    ]
    return ok({"session_id": sid, "rows_saved": len(results)})


@app.route("/api/sessions/<sid>", methods=["GET"])
@optional_auth
def get_session(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    return ok({
        "session":          SESSIONS[sid],
        "results":          TEST_RESULTS.get(sid, []),
        "settings":         SCREEN_SETTINGS.get(sid),
        "image":            (EYE_IMAGES.get(sid) or [None])[-1],
        "settings_history": list(reversed(SETTINGS_HISTORY.get(sid, [])))[:10],
    })


@app.route("/api/sessions", methods=["GET"])
@login_required
def list_sessions():
    uid  = g.current_user["id"]
    rows = sorted(
        [s for s in SESSIONS.values() if s.get("user_id") == uid],
        key=lambda s: s["created_at"], reverse=True
    )[:50]
    return ok(rows)


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@login_required
def delete_session(sid: str):
    sess = SESSIONS.get(sid)
    if not sess or sess.get("user_id") != g.current_user["id"]:
        return err("Session not found or not yours", 404)

    for store in (SESSIONS, TEST_RESULTS, SCREEN_SETTINGS, SETTINGS_HISTORY, EYE_IMAGES):
        store.pop(sid, None)
    return ok({"deleted": sid})

# ──────────────────────────────────────────────────────────────
#  ROUTES — SCREEN SETTINGS
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/settings", methods=["POST"])
@optional_auth
def save_settings(sid: str):
    """
    POST /api/sessions/<sid>/settings

    Body (all optional):
      brightness     : 10-100    (default from profile)
      font_size      : 12-28     (default from profile)
      blue_filter    : 0-100     (default from profile)
      display_zoom   : 75-200    (default from profile)
      dark_mode      : bool
      contrast       : Low|Medium|High|Max
      auto_sunset    : bool
      reduce_white   : bool
      text_spacing   : bool
      vision_profile : normal|mild|moderate|high
      apply_to_os    : bool  — on Vercel this is always false (no subprocess)
    """
    if sid not in SESSIONS:
        return err("Session not found", 404)

    data    = request.get_json(silent=True) or {}
    sess    = SESSIONS[sid]

    vision_profile = data.get("vision_profile") or recommend_profile_from_score(
        sess.get("overall_score", 0),
        sess.get("best_acuity", 0.0),
    )

    pdef          = VISION_PROFILES.get(vision_profile, VISION_PROFILES["mild"])
    brightness    = max(10,  min(100, int(data.get("brightness",   pdef["brightness"]))))
    font_size     = max(12,  min(28,  int(data.get("font_size",     pdef["font_size"]))))
    blue_filter   = max(0,   min(100, int(data.get("blue_filter",   pdef["blue_filter"]))))
    display_zoom  = max(75,  min(200, int(data.get("display_zoom",  pdef["display_zoom"]))))
    dark_mode     = bool(data.get("dark_mode",    pdef["dark_mode"]))
    contrast      = str(data.get("contrast",      pdef["contrast"]))
    auto_sunset   = bool(data.get("auto_sunset",  pdef["auto_sunset"]))
    reduce_white  = bool(data.get("reduce_white", pdef["reduce_white"]))
    text_spacing  = bool(data.get("text_spacing", pdef["text_spacing"]))

    if contrast not in ("Low", "Medium", "High", "Max"):
        contrast = "Medium"

    settings = {
        "session_id": sid, "brightness": brightness, "font_size": font_size,
        "blue_filter": blue_filter, "display_zoom": display_zoom,
        "dark_mode": dark_mode, "contrast": contrast,
        "auto_sunset": auto_sunset, "reduce_white": reduce_white,
        "text_spacing": text_spacing, "vision_profile": vision_profile,
        "applied_at": now_str(),
    }
    SCREEN_SETTINGS[sid] = settings

    # OS apply — not available on Vercel serverless (no subprocess)
    # Frontend JavaScript handles browser-level CSS filter application.
    apply_result = {
        "applied": False,
        "reason": (
            "OS-level apply is not available on Vercel serverless. "
            "Use the frontend Apply Settings button to apply CSS filters in-browser, "
            "or run the backend locally (python app.py) for full OS-level control."
        ),
        "os": "serverless",
    }

    hist_entry = {**settings, "apply_result": json.dumps(apply_result)}
    SETTINGS_HISTORY.setdefault(sid, []).append(hist_entry)

    return ok({
        "session_id":          sid,
        "settings":            settings,
        "recommended_profile": vision_profile,
        "apply_result":        apply_result,
        "os_detected":         "serverless (Vercel)",
    })


@app.route("/api/sessions/<sid>/settings", methods=["GET"])
@optional_auth
def get_settings(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)
    return ok({
        "settings": SCREEN_SETTINGS.get(sid),
        "history":  list(reversed(SETTINGS_HISTORY.get(sid, [])))[:20],
        "profiles": VISION_PROFILES,
    })


@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    return ok(VISION_PROFILES)


@app.route("/api/sessions/<sid>/settings/recommend", methods=["GET"])
@optional_auth
def recommend_settings(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    sess         = SESSIONS[sid]
    profile_name = recommend_profile_from_score(
        sess.get("overall_score", 0), sess.get("best_acuity", 0.0)
    )
    profile      = VISION_PROFILES[profile_name].copy()
    profile["vision_profile"] = profile_name

    strain_risk   = sess.get("strain_risk",   0)
    dryness_index = sess.get("dryness_index", 0)
    blue_light    = sess.get("blue_light",    0)

    if strain_risk   > 70: profile["brightness"]  = max(10,  profile["brightness"]  - 10)
    if dryness_index > 60: profile["blue_filter"]  = min(100, profile["blue_filter"]  + 10)
    if blue_light    > 80: profile["blue_filter"]  = min(100, profile["blue_filter"]  + 5)

    reasoning = []
    if sess.get("best_acuity",    1.0) < 0.5:  reasoning.append("Visual acuity below 20/40 — larger font and higher zoom recommended.")
    if strain_risk   > 70:                      reasoning.append("High eye strain risk detected — reduced brightness applied.")
    if dryness_index > 60:                      reasoning.append("Elevated dryness index — stronger blue light filter applied.")
    if sess.get("overall_score", 100) < 50:     reasoning.append("Low overall score — maximum accessibility settings recommended.")

    return ok({
        "session_id":   sid,
        "profile_name": profile_name,
        "settings":     profile,
        "reasoning":    reasoning,
        "test_summary": {
            "overall_score": sess.get("overall_score"),
            "best_acuity":   sess.get("best_acuity"),
            "best_fraction": sess.get("best_fraction"),
            "levels_passed": sess.get("levels_passed"),
            "strain_risk":   strain_risk,
            "dryness_index": dryness_index,
            "blue_light":    blue_light,
            "grade":         sess.get("grade"),
        },
    })


@app.route("/api/sessions/<sid>/settings/apply-profile", methods=["POST"])
@optional_auth
def apply_profile(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    data    = request.get_json(silent=True) or {}
    profile = data.get("profile", "mild")
    if profile not in VISION_PROFILES:
        return err(f"Unknown profile. Valid: {list(VISION_PROFILES.keys())}")

    payload              = VISION_PROFILES[profile].copy()
    payload["vision_profile"] = profile

    # Build a fake request body and call save_settings directly
    original_json = request.get_json
    request.get_json = lambda **kw: payload  # type: ignore[method-assign]
    try:
        result = save_settings(sid)
    finally:
        request.get_json = original_json  # type: ignore[method-assign]
    return result

# ──────────────────────────────────────────────────────────────
#  ROUTES — EYE IMAGE + AI ANALYSIS
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/eye-image", methods=["POST"])
@optional_auth
def upload_eye_image(sid: str):
    """
    POST /api/sessions/<sid>/eye-image
    JSON body: { image_base64: "data:image/jpeg;base64,..." }
    OR multipart form with field "image"
    """
    if sid not in SESSIONS:
        return err("Session not found", 404)

    if request.content_type and "multipart" in request.content_type:
        f = request.files.get("image")
        if not f:
            return err("No image file provided")
        raw  = f.read()
        b64  = base64.b64encode(raw).decode()
        mime = f.mimetype or "image/jpeg"
    else:
        data  = request.get_json(silent=True) or {}
        b64_d = data.get("image_base64", "")
        if not b64_d:
            return err("No image_base64 provided")
        if "," in b64_d:
            header, b64 = b64_d.split(",", 1)
            mime = header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
        else:
            b64, mime = b64_d, "image/jpeg"

    analysis = analyse_eye(b64, mime)

    image_record = {
        "session_id": sid, "filename": f"{sid}_{int(time.time())}.jpg",
        "analysis": analysis,
        "redness":  analysis.get("redness_score", 0),
        "dryness":  analysis.get("dryness_score", 0),
        "anomaly":  analysis.get("anomaly_score", 0),
        "created_at": now_str(),
    }
    EYE_IMAGES.setdefault(sid, []).append(image_record)

    sess = SESSIONS[sid]
    sess["has_eye_image"] = 1
    sess["ai_analysis"]   = json.dumps(analysis)

    return ok({"session_id": sid, "analysis": analysis})

# ──────────────────────────────────────────────────────────────
#  ROUTES — PDF REPORT
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/report.pdf", methods=["GET"])
@optional_auth
def download_pdf(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    sess     = SESSIONS[sid]
    rows     = TEST_RESULTS.get(sid, [])
    settings = SCREEN_SETTINGS.get(sid, {})

    user_name = "Guest"
    uid = sess.get("user_id")
    if uid and uid in USERS:
        u = USERS[uid]
        user_name = f"{u['name']}  ·  Age {u.get('age','?')}  ·  {u.get('gender','?')}"

    pdf_bytes = build_pdf(sess, rows, settings, user_name)
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    return send_file(
        buf, mimetype="application/pdf", as_attachment=True,
        download_name=f"NeuroVision_Report_{sid[:8]}.pdf",
    )


def build_pdf(sess, rows, settings, user_name):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)

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
    story.append(Paragraph("NeuroVision AI", h1))
    story.append(Paragraph("Vision Health Report",
        ParagraphStyle("", fontName="Helvetica-Bold", fontSize=15, textColor=BLUE, spaceAfter=2)))
    story.append(HRFlowable(width="100%", thickness=2, color=TEAL, spaceAfter=8))

    # Patient info
    story.append(Paragraph("Patient Information", h2))
    info_tbl = Table([
        ["Patient",    user_name],
        ["Session ID", sess["id"]],
        ["Date",       sess.get("created_at","—")[:16]],
        ["Eye Mode",   sess.get("eye_mode","both").capitalize()],
    ], colWidths=[40*mm, 130*mm])
    info_tbl.setStyle(TableStyle([
        ("FONTNAME",       (0,0),(-1,-1),"Helvetica"),
        ("FONTSIZE",       (0,0),(-1,-1), 9),
        ("TEXTCOLOR",      (0,0),(0,-1),  GRAY),
        ("TEXTCOLOR",      (1,0),(1,-1),  NAVY),
        ("FONTNAME",       (1,0),(1,-1),  "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,0),(-1,-1), [colors.HexColor("#f8fafc"), WHITE]),
        ("BOTTOMPADDING",  (0,0),(-1,-1), 5),
        ("TOPPADDING",     (0,0),(-1,-1), 5),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 8))

    # Score summary
    story.append(Paragraph("Overall Score", h2))
    score = sess.get("overall_score", 0)
    sc    = GREEN if score >= 80 else (BLUE if score >= 60 else RED)
    s_tbl = Table([
        [f"Overall Score: {score}/100",               f"Grade: {sess.get('grade','Unknown')}"],
        [f"Levels Passed: {sess.get('levels_passed',0)}/11",
         f"Best Acuity: {sess.get('best_acuity',0)} ({sess.get('best_fraction','—')})"],
        [f"Eye Strain Risk: {sess.get('strain_risk',0)}%",
         f"Blue Light: {sess.get('blue_light',0)}%"],
    ], colWidths=[85*mm, 85*mm])
    s_tbl.setStyle(TableStyle([
        ("FONTNAME",      (0,0),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 10),
        ("TEXTCOLOR",     (0,0),(0, 0),  sc),
        ("TEXTCOLOR",     (0,1),(-1,-1), NAVY),
        ("BACKGROUND",    (0,0),(-1,-1), colors.HexColor("#f0f9ff")),
        ("GRID",          (0,0),(-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 10))

    # Snellen table
    story.append(Paragraph("Snellen Chart Results — All 11 Levels", h2))
    tdata = [["Acuity","Fraction","Level","Target","Answer","Result"]]
    for r in rows:
        tdata.append([
            str(r.get("acuity","—")),    str(r.get("fraction","—")),
            str(r.get("level_name","—")), str(r.get("target_letters","—")),
            str(r.get("chosen_answer","—")),
            "✓ PASS" if r.get("passed") else "✗ FAIL",
        ])
    tbl = Table(tdata, colWidths=[22*mm,22*mm,38*mm,30*mm,25*mm,18*mm], repeatRows=1)
    tstyle = [
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 8),
        ("ALIGN",         (0,0),(-1,-1),"CENTER"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#f8fafc"),WHITE]),
        ("GRID",          (0,0),(-1,-1), 0.3, colors.HexColor("#e2e8f0")),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]
    for i, r in enumerate(rows, 1):
        c = GREEN if r.get("passed") else RED
        tstyle += [("TEXTCOLOR",(5,i),(5,i),c),("FONTNAME",(5,i),(5,i),"Helvetica-Bold")]
    tbl.setStyle(TableStyle(tstyle))
    story.append(tbl)
    story.append(Spacer(1, 10))

    # Screen settings
    if settings:
        story.append(Paragraph("Screen Settings Applied", h2))
        st = Table([
            ["Brightness",  f"{settings.get('brightness',45)}%",   "Font Size",    f"{settings.get('font_size',16)}px"],
            ["Blue Filter", f"{settings.get('blue_filter',65)}%",  "Display Zoom", f"{settings.get('display_zoom',110)}%"],
            ["Dark Mode",   "Yes" if settings.get("dark_mode") else "No",
             "Contrast",   str(settings.get("contrast","Medium"))],
            ["Profile",    str(settings.get("vision_profile","mild")),
             "Applied At", str(settings.get("applied_at","—"))[:16]],
        ], colWidths=[35*mm,30*mm,35*mm,30*mm])
        st.setStyle(TableStyle([
            ("FONTNAME",       (0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",       (0,0),(-1,-1), 9),
            ("TEXTCOLOR",      (0,0),(0,-1),  GRAY),
            ("TEXTCOLOR",      (2,0),(2,-1),  GRAY),
            ("FONTNAME",       (1,0),(1,-1),  "Helvetica-Bold"),
            ("FONTNAME",       (3,0),(3,-1),  "Helvetica-Bold"),
            ("TEXTCOLOR",      (1,0),(1,-1),  BLUE),
            ("TEXTCOLOR",      (3,0),(3,-1),  BLUE),
            ("ROWBACKGROUNDS", (0,0),(-1,-1), [colors.HexColor("#f8fafc"),WHITE]),
            ("GRID",           (0,0),(-1,-1), 0.3, colors.HexColor("#e2e8f0")),
            ("BOTTOMPADDING",  (0,0),(-1,-1), 6),
            ("TOPPADDING",     (0,0),(-1,-1), 6),
            ("LEFTPADDING",    (0,0),(-1,-1), 8),
        ]))
        story.append(st)
        story.append(Spacer(1, 10))

    # Recommendations
    story.append(Paragraph("Personalized Recommendations", h2))
    recs = [
        "Apply the 20-20-20 rule: every 20 min, look 20 ft away for 20 seconds.",
        f"Enable blue light filter at {sess.get('blue_light',65)}% on all screens.",
        "Maintain 50–70 cm screen distance for optimal viewing comfort.",
        "Stay well-hydrated — dehydration directly affects eye lubrication.",
        "Schedule an annual comprehensive eye exam with a licensed optometrist.",
    ]
    if sess.get("best_acuity", 1.0) < 1.0:
        recs.insert(0, "⚠  Vision below 20/20 — consult an optometrist for corrective lenses.")
    for r in recs:
        story.append(Paragraph(f"• {r}", bod))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceAfter=6))
    story.append(Paragraph(
        "Generated by NeuroVision AI  ·  For informational screening only. "
        "Not a medical diagnosis. Consult a licensed ophthalmologist for professional evaluation.", sml))

    doc.build(story)
    return buf.getvalue()

# ──────────────────────────────────────────────────────────────
#  ROUTES — TEXT REPORT
# ──────────────────────────────────────────────────────────────

@app.route("/api/sessions/<sid>/report.txt", methods=["GET"])
@optional_auth
def download_txt(sid: str):
    if sid not in SESSIONS:
        return err("Session not found", 404)

    sess     = SESSIONS[sid]
    rows     = TEST_RESULTS.get(sid, [])
    settings = SCREEN_SETTINGS.get(sid, {})
    uid      = sess.get("user_id")
    user_name = USERS[uid]["name"] if uid and uid in USERS else "Guest"

    lines = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║         NEUROVISION AI — COMPLETE VISION HEALTH REPORT       ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        "PATIENT INFORMATION",  "─"*40,
        f"  Patient      : {user_name}",
        f"  Session ID   : {sess['id']}",
        f"  Date         : {sess.get('created_at','—')[:16]}",
        f"  Eye Mode     : {sess.get('eye_mode','both')}",
        "",
        f"OVERALL SCORE  :  {sess.get('overall_score',0)} / 100  ({sess.get('grade','—')})",
        "",
        "SNELLEN CHART RESULTS", "─"*70,
        f"  {'Acuity':<8} {'Fraction':<10} {'Level':<22} {'Target':<22} {'Answer':<12} Result",
        f"  {'─'*7} {'─'*9} {'─'*21} {'─'*21} {'─'*11} {'─'*6}",
    ]
    for r in rows:
        lines.append(
            f"  {str(r.get('acuity','')):<8} {str(r.get('fraction','')):<10} "
            f"{str(r.get('level_name','')):<22} {str(r.get('target_letters','')):<22} "
            f"{str(r.get('chosen_answer','')):<12} {'PASS' if r.get('passed') else 'FAIL'}"
        )

    if settings:
        lines += [
            "", "SCREEN SETTINGS APPLIED", "─"*40,
            f"  Vision Profile : {settings.get('vision_profile','mild')}",
            f"  Brightness     : {settings.get('brightness',45)}%",
            f"  Font Size      : {settings.get('font_size',16)}px",
            f"  Blue Filter    : {settings.get('blue_filter',65)}%",
            f"  Display Zoom   : {settings.get('display_zoom',110)}%",
            f"  Dark Mode      : {'Yes' if settings.get('dark_mode') else 'No'}",
            f"  Contrast       : {settings.get('contrast','Medium')}",
            f"  Applied At     : {settings.get('applied_at','—')[:16]}",
        ]

    lines += [
        "", "SUMMARY", "─"*40,
        f"  Levels Passed  : {sess.get('levels_passed',0)} / 11",
        f"  Best Acuity    : {sess.get('best_acuity',0)} ({sess.get('best_fraction','—')})",
        f"  Eye Strain Risk: {sess.get('strain_risk',0)}%",
        f"  Blue Light     : {sess.get('blue_light',0)}%",
        "", "─"*70,
        "Generated by NeuroVision AI · For informational purposes only.",
        "─"*70,
    ]

    return send_file(
        io.BytesIO("\n".join(lines).encode()), mimetype="text/plain",
        as_attachment=True, download_name=f"NeuroVision_Report_{sid[:8]}.txt",
    )

# ──────────────────────────────────────────────────────────────
#  ROUTES — STATISTICS
# ──────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
@login_required
def user_stats():
    uid  = g.current_user["id"]
    rows = sorted(
        [s for s in SESSIONS.values() if s.get("user_id") == uid],
        key=lambda s: s["created_at"]
    )[:20]
    best_acuity = max((s.get("best_acuity",  0) for s in rows), default=0)
    best_score  = max((s.get("overall_score",0) for s in rows), default=0)
    most_passed = max((s.get("levels_passed",0) for s in rows), default=0)
    return ok({
        "trend": rows,
        "best":  {"best_acuity": best_acuity, "best_score": best_score, "most_passed": most_passed},
        "total_sessions": len(rows),
    })


@app.route("/api/stats/global", methods=["GET"])
def global_stats():
    sessions = list(SESSIONS.values())
    n = len(sessions)
    return ok({
        "total_sessions": n,
        "avg_score":  sum(s.get("overall_score",0) for s in sessions) / n if n else 0,
        "avg_passed": sum(s.get("levels_passed",0) for s in sessions) / n if n else 0,
        "top_acuity": max((s.get("best_acuity",0) for s in sessions), default=0),
    })

# ──────────────────────────────────────────────────────────────
#  HEALTH CHECK
# ──────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return ok({
        "status":      "ok",
        "version":     "2.1.0",
        "platform":    "vercel-serverless",
        "ai_provider": AI_PROVIDER,
        "ai_enabled":  bool(GEMINI_KEY or ANTHROPIC_KEY),
        "timestamp":   datetime.utcnow().isoformat() + "Z",
        "note": (
            "Running on Vercel serverless. Data is in-memory only and resets "
            "on cold starts. For persistent storage, add a hosted DB (Supabase/PlanetScale)."
        ),
    })

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
    print("""
╔══════════════════════════════════════════════════════════════╗
║     NEUROVISION AI  v2.1  —  VERCEL-COMPATIBLE SERVER        ║
╠══════════════════════════════════════════════════════════════╣
║  Running locally at http://localhost:5000                    ║
║                                                              ║
║  AUTH                                                        ║
║    POST  /api/auth/register                                  ║
║    POST  /api/auth/login                                     ║
║    POST  /api/auth/logout                                    ║
║    GET   /api/auth/me                                        ║
║    PUT   /api/auth/update                                    ║
║    POST  /api/auth/change-password                           ║
║                                                              ║
║  SESSIONS                                                    ║
║    POST  /api/sessions                                       ║
║    GET   /api/sessions              (auth)                   ║
║    GET   /api/sessions/<id>                                  ║
║    POST  /api/sessions/<id>/results                          ║
║    DELETE /api/sessions/<id>        (auth)                   ║
║                                                              ║
║  SCREEN SETTINGS                                             ║
║    POST  /api/sessions/<id>/settings                         ║
║    GET   /api/sessions/<id>/settings                         ║
║    GET   /api/sessions/<id>/settings/recommend               ║
║    POST  /api/sessions/<id>/settings/apply-profile           ║
║    GET   /api/profiles                                       ║
║                                                              ║
║  EYE IMAGE & REPORTS                                         ║
║    POST  /api/sessions/<id>/eye-image                        ║
║    GET   /api/sessions/<id>/report.pdf                       ║
║    GET   /api/sessions/<id>/report.txt                       ║
║                                                              ║
║  STATS & HEALTH                                              ║
║    GET   /api/stats                 (auth)                   ║
║    GET   /api/stats/global                                   ║
║    GET   /api/health                                         ║
╚══════════════════════════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=PORT, debug=True)
