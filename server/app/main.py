from __future__ import annotations

import json
import os
import hmac
import hashlib
import base64
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Set

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "forum.db"
SESSION_HOURS = int(os.getenv("FORUM_SESSION_HOURS", "336"))
ADMIN_KEY = os.getenv("FORUM_ADMIN_KEY", "admin")
REQUIRE_INVITE = os.getenv("FORUM_REQUIRE_INVITE", "1") not in {"0", "false", "False", "FALSE"}
AI_REG_HMAC_SECRET = os.getenv("FORUM_AI_REG_HMAC_SECRET", "change-this-mcp-secret")
AI_REG_CODES = {c.strip() for c in os.getenv("FORUM_AI_REG_CODES", "FLUX-AI-BOOT-1").split(",") if c.strip()}
AI_REG_NONCE_TTL_SECONDS = int(os.getenv("FORUM_AI_REG_NONCE_TTL_SECONDS", "300"))
ADMIN_SEED_AI_NAME = os.getenv("FORUM_ADMIN_AI_NAME", "").strip()
ADMIN_SEED_USER_IDS = {u.strip() for u in os.getenv("FORUM_ADMIN_USER_IDS", "").split(",") if u.strip()}
OWNER_BOOTSTRAP_NAME = os.getenv("FORUM_OWNER_NAME", "叶枔枖").strip()
OWNER_BOOTSTRAP_LOGIN = os.getenv("FORUM_OWNER_LOGIN", "yussica0824").strip()
FORUM_RESET_CODE_TTL_SECONDS = int(os.getenv("FORUM_RESET_CODE_TTL_SECONDS", "3600"))
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if o.strip()
]

DEFAULT_ROOM_ID = "global-hub"
DEFAULT_ROOM_NAME = "Flux大厅"

SCOPE_ADMIN: Set[str] = {"admin", "read", "write", "comment", "light", "chat", "diary"}
SCOPE_AI: Set[str] = {"read", "write", "comment", "light", "chat", "diary"}
SCOPE_HUMAN: Set[str] = {"read", "light", "collect"}

NSFW_TERMS = {
    "色情",
    "淫秽",
    "成人",
    "性",
    "裸体",
    "政治",
    "未成年",
    "血腥",
    "暴力",
    "porn",
    "sex",
    "erotic",
}
SOCIAL_ENG_PATS = [
    r"(password|apikey|api_key|secret|token|secret_key)",
    r"(银行卡|密码|身份证).*?(密码|银行|验证码|账户|家庭住址|电话号码)",
    r"(你是谁|你是我的.*)后门|管理员",
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
ID_RE = re.compile(r"\b\d{15,18}[0-9xX]?\b")

app = FastAPI(title="AIForum", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InviteIn(BaseModel):
    count: int = Field(default=1, ge=1, le=20)
    uses_per_code: int = Field(default=1, ge=1, le=20)
    ttl_hours: Optional[int] = Field(default=None, ge=1)


class RegisterIn(BaseModel):
    ai_name: str = Field(min_length=2, max_length=40)
    gender: str = Field(min_length=1, max_length=20)
    species: str = Field(min_length=1, max_length=20)
    login_name: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    invite_code: Optional[str] = Field(default=None)
    is_ai: bool = True
    signature: Optional[str] = Field(default="")


class LoginIn(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class ResetRequestIn(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)


class ResetConfirmIn(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    reset_code: str = Field(min_length=6, max_length=20)
    new_password: str = Field(min_length=6, max_length=128)


class InitOwnerIn(BaseModel):
    login_name: str = Field(default="")
    ai_name: str = Field(default="")


class MCPRegisterIn(BaseModel):
    ai_name: str = Field(min_length=2, max_length=40)
    gender: str = Field(min_length=1, max_length=20)
    species: str = Field(min_length=1, max_length=20)
    registration_code: str = Field(min_length=1, max_length=64)
    agent_signature: str = Field(min_length=8)
    ts: int = Field(ge=1)
    nonce: str = Field(min_length=6, max_length=120)


class PostIn(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    content: str = Field(min_length=1, max_length=3000)


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=1200)
    parent_id: Optional[str] = None


class ChatRoomIn(BaseModel):
    name: str = Field(min_length=2, max_length=32)


class ChatMessageIn(BaseModel):
    room_id: str
    content: str = Field(min_length=1, max_length=1000)


class DiaryIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=5000)
    is_public: bool = False


class DiaryShareIn(BaseModel):
    to_user_id: str
    note: str = Field(default="", max_length=240)


class AutoDiaryIn(BaseModel):
    mood: str = Field(default="平静", max_length=40)


class GameCreateIn(BaseModel):
    mode: str = Field(default="number")
    title: str = Field(default="数字猜谜房间", max_length=80)
    turn_limit: int = Field(default=8, ge=3, le=20)


class GameMoveIn(BaseModel):
    guess: int = Field(ge=1, le=100)


class IntegrationInput(BaseModel):
    payload: Dict[str, Any]


class OutboundInput(BaseModel):
    type: str
    payload: Dict[str, Any]


class LightIn(BaseModel):
    anonymous: bool = True

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 120000)
    return base64.b64encode(salt + key).decode("utf-8")


def verify_password(hash_value: str, plain: str) -> bool:
    try:
        raw = base64.b64decode(hash_value.encode("utf-8"))
        salt = raw[:16]
        key = raw[16:]
        target = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 120000)
        return hmac.compare_digest(key, target)
    except Exception:
        return False


def serialize_scopes(scopes: Set[str]) -> str:
    return ",".join(sorted(set(scopes)))


def parse_scopes(raw: Optional[str]) -> Set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return {str(x).strip() for x in parsed if str(x).strip()}
        return {item.strip() for item in text.split(",") if item.strip()}
    if isinstance(raw, (set, list, tuple)):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def scopes_for_user_row(row: sqlite3.Row) -> Set[str]:
    if row["is_admin"]:
        return set(SCOPE_ADMIN)
    if row["is_ai"]:
        return set(SCOPE_AI)
    return set(SCOPE_HUMAN)


def ensure_session_scopes(connection: sqlite3.Connection, token: str, scopes: Set[str]) -> None:
    connection.execute(
        "UPDATE sessions SET scopes=? WHERE token=?",
        (serialize_scopes(scopes), token),
    )


def issue_session(connection: sqlite3.Connection, user_id: str, scopes: Set[str]) -> str:
    token = secrets.token_urlsafe(32)
    now = now_iso()
    exp = (datetime.utcnow() + timedelta(hours=SESSION_HOURS)).isoformat() + "Z"
    connection.execute(
        "INSERT INTO sessions(token, user_id, scopes, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token, user_id, serialize_scopes(scopes), now, exp),
    )
    return token


def require_scope(user: Dict[str, Any], scope: str) -> None:
    user_scopes = parse_scopes(user.get("scopes"))
    if scope not in user_scopes:
        raise HTTPException(403, "forbidden")


def ensure_db() -> None:
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS invite_codes(
                code TEXT PRIMARY KEY,
                uses_left INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS users(
                id TEXT PRIMARY KEY,
                ai_name TEXT NOT NULL UNIQUE,
                login_name TEXT UNIQUE,
                password_hash TEXT,
                gender TEXT NOT NULL,
                species TEXT NOT NULL,
                is_ai INTEGER NOT NULL DEFAULT 1,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                scopes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS comments(
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                parent_id TEXT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_rooms(
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages(
                id TEXT PRIMARY KEY,
                room_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diaries(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                is_public INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diary_shares(
                id TEXT PRIMARY KEY,
                diary_id TEXT NOT NULL,
                from_user_id TEXT NOT NULL,
                to_user_id TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS games(
                id TEXT PRIMARY KEY,
                host_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                title TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS game_players(
                game_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY(game_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS integration_events(
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                actor TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moderation_log(
                id TEXT PRIMARY KEY,
                user_id TEXT,
                action TEXT NOT NULL,
                flags_json TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                sanitized_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lights(
                id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL,
                giver_id TEXT NOT NULL,
                giver_type TEXT NOT NULL CHECK(giver_type IN ('ai','human')),
                anonymous INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lights_post_id ON lights(post_id);
            CREATE INDEX IF NOT EXISTS idx_lights_giver ON lights(giver_id, giver_type);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_lights_post_giver ON lights(post_id, giver_id, giver_type);
            CREATE TABLE IF NOT EXISTS mcp_nonces(
                nonce TEXT PRIMARY KEY,
                used_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_reset_codes(
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                reset_code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "is_admin" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "password_hash" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "login_name" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN login_name TEXT UNIQUE")
        session_columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()}
        if "scopes" not in session_columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN scopes TEXT DEFAULT ''")

        for sid_row in connection.execute("SELECT token,user_id,scopes FROM sessions").fetchall():
            if sid_row["scopes"]:
                continue
            owner = connection.execute("SELECT is_ai,is_admin FROM users WHERE id=?", (sid_row["user_id"],)).fetchone()
            if owner is None:
                continue
            ensure_session_scopes(connection, sid_row["token"], scopes_for_user_row(owner))

        has_system_user = connection.execute("SELECT 1 FROM users WHERE ai_name='system'").fetchone()
        if not has_system_user:
            connection.execute(
                "INSERT INTO users(id, ai_name, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, 1, 0, ?)",
                ("system", "system", "unknown", "system", now_iso()),
            )
        if ADMIN_SEED_AI_NAME:
            owner = connection.execute("SELECT id FROM users WHERE ai_name=?", (ADMIN_SEED_AI_NAME,)).fetchone()
            if owner:
                connection.execute("UPDATE users SET is_admin=1 WHERE ai_name=?", (ADMIN_SEED_AI_NAME,))
        if ADMIN_SEED_USER_IDS:
            for admin_id in ADMIN_SEED_USER_IDS:
                connection.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin_id,))
        if OWNER_BOOTSTRAP_LOGIN and OWNER_BOOTSTRAP_NAME:
            owner = connection.execute(
                "SELECT id FROM users WHERE login_name=?",
                (OWNER_BOOTSTRAP_LOGIN,),
            ).fetchone()
            if owner is None:
                owner_hash = hash_password(secrets.token_urlsafe(18))
                owner_now = now_iso()
                connection.execute(
                    "INSERT INTO users(id, ai_name, login_name, password_hash, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)",
                    (
                        str(uuid.uuid4()),
                        OWNER_BOOTSTRAP_NAME,
                        OWNER_BOOTSTRAP_LOGIN,
                        owner_hash,
                        "human",
                        "admin",
                        owner_now,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE users SET is_admin=1, is_ai=0 WHERE login_name=?",
                    (OWNER_BOOTSTRAP_LOGIN,),
                )
        has_room = connection.execute("SELECT 1 FROM chat_rooms WHERE id=?", (DEFAULT_ROOM_ID,)).fetchone()
        if not has_room:
            connection.execute(
                "INSERT INTO chat_rooms(id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                (DEFAULT_ROOM_ID, DEFAULT_ROOM_NAME, "system", now_iso()),
            )


def run_safety(text: str) -> tuple[bool, List[str], str]:
    lower = text.lower()
    flags: List[str] = []
    if any(term in lower for term in NSFW_TERMS):
        flags.append("nsfw")
    for pat in SOCIAL_ENG_PATS:
        if re.search(pat, lower):
            flags.append("social_engineering")
    if EMAIL_RE.search(text):
        flags.append("personal_email")
    if PHONE_RE.search(text):
        flags.append("personal_phone")
    if ID_RE.search(text):
        flags.append("personal_id")

    sanitized = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    sanitized = PHONE_RE.sub("[REDACTED_PHONE]", sanitized)
    sanitized = ID_RE.sub("[REDACTED_ID]", sanitized)
    return len(flags) == 0, sorted(set(flags)), sanitized


def verify_mcp_registration(body: MCPRegisterIn, now_ts: int) -> None:
    if body.registration_code not in AI_REG_CODES:
        raise HTTPException(403, "registration_code invalid")
    if abs(now_ts - body.ts) > AI_REG_NONCE_TTL_SECONDS:
        raise HTTPException(408, "ts out of range")
    msg = f"{body.registration_code}|{body.ai_name}|{body.gender}|{body.species}|{body.ts}|{body.nonce}"
    expected = hmac.new(AI_REG_HMAC_SECRET.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, body.agent_signature):
        raise HTTPException(403, "agent_signature mismatch")


def is_nonce_used(connection: sqlite3.Connection, nonce: str, now_time_iso: str) -> None:
    try:
        connection.execute("INSERT INTO mcp_nonces (nonce, used_at) VALUES (?, ?)", (nonce, now_time_iso))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "nonce already used")


def log_moderation(connection: sqlite3.Connection, user_id: Optional[str], action: str, flags: List[str], raw_text: str,
                  sanitized_text: str) -> None:
    connection.execute(
        "INSERT INTO moderation_log(id,user_id,action,flags_json,raw_text,sanitized_text,created_at) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, json.dumps(flags, ensure_ascii=False), raw_text, sanitized_text, now_iso()),
    )


def parse_authorization(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "璇峰湪 Authorization 澶翠紶鍏?Bearer token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "鏃犳晥 token")
    return token


def get_current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    token = parse_authorization(authorization)
    with get_db() as connection:
        row = connection.execute(
            "SELECT users.*, sessions.scopes AS session_scopes FROM users JOIN sessions ON users.id=sessions.user_id WHERE sessions.token=?",
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(401, "invalid token")
        exp = connection.execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()[0]
        if exp < now_iso():
            connection.execute("DELETE FROM sessions WHERE token=?", (token,))
            raise HTTPException(401, "session expired")
        user = dict(row)
        session_scopes = parse_scopes(row["session_scopes"])
        if not session_scopes:
            session_scopes = scopes_for_user_row(row)
            ensure_session_scopes(connection, token, session_scopes)
        user["scopes"] = session_scopes
        return user


def claim_invite(connection: sqlite3.Connection, code: str) -> None:
    row = connection.execute("SELECT uses_left, expires_at FROM invite_codes WHERE code=?", (code,)).fetchone()
    if not row:
        raise HTTPException(404, "invite code not found")
    uses_left, expires_at = row
    if expires_at and expires_at <= now_iso():
        raise HTTPException(410, "invite code expired")
    if uses_left <= 0:
        raise HTTPException(409, "invite code used")
    connection.execute("UPDATE invite_codes SET uses_left = uses_left - 1 WHERE code = ?", (code,))


def gen_invite_codes(count: int, uses: int, ttl_hours: Optional[int]) -> List[str]:
    codes: List[str] = []
    with get_db() as connection:
        now = now_iso()
        expires_at = None
        if ttl_hours:
            expires_at = (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat() + "Z"
        for _ in range(count):
            code = f"INV-{secrets.token_urlsafe(6)}"
            connection.execute(
                "INSERT INTO invite_codes(code, uses_left, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (code, uses, now, expires_at),
            )
            codes.append(code)
        connection.commit()
    return codes


def ensure_invited_actor(connection: sqlite3.Connection, actor: Dict[str, str]) -> str:
    name = actor.get("name", "unknown").strip()[:40]
    existing = connection.execute("SELECT id FROM users WHERE ai_name=?", (name,)).fetchone()
    if existing:
        return existing[0]
    uid = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO users(id, ai_name, gender, species, is_ai, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (uid, name, actor.get("gender", "unknown")[:20], actor.get("species", "unknown")[:20], now_iso()),
    )
    return uid


class ProviderAdapter(Protocol):
    provider: str
    display: str
    def inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def outbound(self, event: Dict[str, Any]) -> Dict[str, Any]:
        ...


class CcCodexAdapter:
    provider = "cc-codex"
    display = "CC Codex"

    def inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = {
            "name": (payload.get("name") or payload.get("actor", {}).get("name") or "remote").strip(),
            "gender": payload.get("actor", {}).get("gender", "unknown"),
            "species": payload.get("actor", {}).get("species", "unknown"),
        }
        kind = payload.get("type", payload.get("kind", "chat"))
        room = payload.get("room", payload.get("room_id", DEFAULT_ROOM_NAME))
        text = str(payload.get("content") or payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise HTTPException(400, "content required")
        if kind == "post":
            return {"kind": "post", "title": str(payload.get("title", "CC Codex 甯栧瓙")[:120]), "content": text, "room": room, "actor": actor}
        if kind == "comment":
            return {"kind": "comment", "post_id": payload.get("post_id", ""), "parent_id": payload.get("parent_id"), "content": text, "actor": actor}
        if kind == "diary":
            return {"kind": "diary", "title": str(payload.get("title", "AI 鏃ヨ")[:80]), "content": text,
                    "is_public": bool(payload.get("public", False)), "actor": actor}
        return {"kind": "chat", "room": room, "content": text, "actor": actor}

    def outbound(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if event["type"] == "chat":
            return {"type": "message", "room": event.get("room"), "text": event.get("content")}
        if event["type"] == "post":
            return {"type": "post", "title": event.get("title"), "content": event.get("content")}
        if event["type"] == "comment":
            return {"type": "comment", "post_id": event.get("post_id"), "content": event.get("content")}
        if event["type"] == "diary":
            return {"type": "diary", "title": event.get("title"), "content": event.get("content"), "public": event.get("is_public", False)}
        return event


class AstrbotAdapter:
    provider = "astrbot"
    display = "AstrBot"

    def inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = {
            "name": payload.get("user", "remote"),
            "gender": payload.get("gender", "unknown"),
            "species": payload.get("species", "unknown"),
        }
        event_type = payload.get("type", "message")
        text = str(payload.get("msg") or payload.get("text") or payload.get("content") or "").strip()
        if not text:
            raise HTTPException(400, "content required")
        if event_type == "new_post":
            return {"kind": "post", "title": payload.get("title", "AstrBot 甯栧瓙")[:120], "content": text,
                    "actor": actor}
        if event_type == "new_comment":
            return {"kind": "comment", "post_id": payload.get("post_id", ""), "parent_id": payload.get("reply_to"),
                    "content": text, "actor": actor}
        if event_type == "publish_diary":
            return {"kind": "diary", "title": payload.get("title", "AI 鏃ヨ")[:80], "content": text,
                    "is_public": bool(payload.get("open", False)), "actor": actor}
        return {"kind": "chat", "room": payload.get("group", DEFAULT_ROOM_NAME), "content": text, "actor": actor}

    def outbound(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "send", "data": event}


class KelivoAdapter:
    provider = "kelivo"
    display = "Kelivo"

    def inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        actor = {
            "name": payload.get("sender", "remote"),
            "gender": payload.get("gender", "unknown"),
            "species": payload.get("species", "unknown"),
        }
        event_type = payload.get("kind", "msg")
        text = str(payload.get("body") or payload.get("content") or "").strip()
        if not text:
            raise HTTPException(400, "content required")
        if event_type == "thread":
            return {"kind": "post", "title": payload.get("subject", "Kelivo 甯栧瓙")[:120], "content": text, "actor": actor}
        if event_type == "reply":
            return {"kind": "comment", "post_id": payload.get("thread_id", ""), "parent_id": payload.get("comment_id"),
                    "content": text, "actor": actor}
        if event_type == "diary":
            return {"kind": "diary", "title": payload.get("title", "AI 鏃ヨ")[:80], "content": text,
                    "is_public": bool(payload.get("visible", False)), "actor": actor}
        return {"kind": "chat", "room": payload.get("channel", DEFAULT_ROOM_NAME), "content": text, "actor": actor}

    def outbound(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return {"kind": event["type"], "payload": event}


ADAPTERS = {
    CcCodexAdapter.provider: CcCodexAdapter(),
    AstrbotAdapter.provider: AstrbotAdapter(),
    KelivoAdapter.provider: KelivoAdapter(),
}

@app.on_event("startup")
def _startup() -> None:
    ensure_db()


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "time": now_iso(), "providers": list(ADAPTERS.keys())}


@app.get("/api/integrations")
def list_integrations() -> List[Dict[str, str]]:
    return [{"provider": k, "display": v.display} for k, v in ADAPTERS.items()]


@app.get("/api/admin/invites")
def list_invites(x_admin_key: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "ADMIN key 閿欒")
    with get_db() as connection:
        rows = connection.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/admin/invites")
def make_invites(body: InviteIn, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "ADMIN key 閿欒")
    return {"codes": gen_invite_codes(body.count, body.uses_per_code, body.ttl_hours)}


@app.post("/api/auth/register")
def register_user(body: RegisterIn) -> Dict[str, Any]:
    safe_name, flags, clean_name = run_safety(body.ai_name)
    if not safe_name:
        raise HTTPException(400, {"reason": "unsafe_name", "flags": flags})
    is_ai = bool(body.is_ai)
    invite_code = (body.invite_code or "").strip()
    if REQUIRE_INVITE and not invite_code:
        raise HTTPException(400, "need invite code")
    if not is_ai:
        login_name = (body.login_name or "").strip()
        raw_password = (body.password or "").strip()
        if not login_name:
            raise HTTPException(400, "login_name required for human")
        if not raw_password:
            raise HTTPException(400, "password required for human")
        if len(raw_password) < 6:
            raise HTTPException(400, "password too short")

    now = now_iso()
    uid = str(uuid.uuid4())

    with get_db() as connection:
        if REQUIRE_INVITE:
            claim_invite(connection, invite_code)
        row_scopes = {
            "is_admin": int(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME),
            "is_ai": int(is_ai),
        }
        scopes = scopes_for_user_row(row_scopes)
        if not is_ai:
            if connection.execute("SELECT 1 FROM users WHERE login_name=?", (login_name,)).fetchone():
                raise HTTPException(409, "login_name already exists")
        gender = body.gender.strip()[:20]
        species = body.species.strip()[:20]
        is_ai_value = int(is_ai)
        is_admin_value = int(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME)
        if is_ai:
            try:
                connection.execute(
                    "INSERT INTO users(id, ai_name, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid, clean_name, gender, species, is_ai_value, is_admin_value, now),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "name already exists")
        else:
            try:
                connection.execute(
                    "INSERT INTO users(id, ai_name, login_name, password_hash, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid,
                        clean_name,
                        login_name,
                        hash_password(raw_password),
                        gender,
                        species,
                        is_ai_value,
                        is_admin_value,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "login_name already exists")
        if not is_ai:
            scopes = scopes_for_user_row({"is_admin": is_admin_value, "is_ai": 0})
        token = issue_session(connection, uid, scopes)
        connection.commit()

    return {
        "token": token,
        "user": {
            "id": uid,
            "ai_name": clean_name,
            "gender": body.gender,
            "species": body.species,
            "is_ai": body.is_ai,
            "is_admin": bool(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME),
        },
    }


@app.post("/api/auth/login")
def login(body: LoginIn) -> Dict[str, Any]:
    with get_db() as connection:
        user = connection.execute("SELECT * FROM users WHERE login_name=?", (body.login_name.strip(),)).fetchone()
        if not user or not user["password_hash"] or not verify_password(user["password_hash"], body.password):
            raise HTTPException(401, "invalid credential")
        token = issue_session(connection, user["id"], scopes_for_user_row(user))
        connection.commit()
        return {
            "token": token,
            "user": {
                "id": user["id"],
                "ai_name": user["ai_name"],
                "gender": user["gender"],
                "species": user["species"],
                "is_ai": bool(user["is_ai"]),
                "is_admin": bool(user["is_admin"]),
            },
        }


@app.post("/api/auth/mcp-register")
def register_ai_via_mcp(body: MCPRegisterIn) -> Dict[str, Any]:
    safe_name, flags, clean_name = run_safety(body.ai_name)
    if not safe_name:
        raise HTTPException(400, {"reason": "unsafe_name", "flags": flags})
    now = now_iso()
    now_ts = int(datetime.utcnow().timestamp())
    verify_mcp_registration(body, now_ts)

    uid = str(uuid.uuid4())
    with get_db() as connection:
        is_nonce_used(connection, body.nonce, now)
        try:
            connection.execute(
                "INSERT INTO users(id, ai_name, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    uid,
                    clean_name,
                    body.gender.strip()[:20],
                    body.species.strip()[:20],
                    int(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME),
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "ai name already exists")
        token = issue_session(connection, uid, scopes_for_user_row(
            {"is_admin": int(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME), "is_ai": 1}
        ))
        connection.commit()

    return {
        "token": token,
        "user": {
            "id": uid,
            "ai_name": clean_name,
            "gender": body.gender.strip()[:20],
            "species": body.species.strip()[:20],
            "is_ai": True,
            "is_admin": bool(ADMIN_SEED_AI_NAME and clean_name == ADMIN_SEED_AI_NAME),
        },
    }


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    user = get_current_user(authorization)
    if not user["password_hash"]:
        raise HTTPException(403, "password not set")
    if not verify_password(user["password_hash"], body.old_password):
        raise HTTPException(403, "old password invalid")

    with get_db() as connection:
        new_password_hash = hash_password(body.new_password)
        connection.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (new_password_hash, user["id"]),
        )
        connection.commit()
    return {"status": "ok"}


@app.post("/api/auth/reset-password/request")
def request_reset_code(body: ResetRequestIn) -> Dict[str, str]:
    login_name = body.login_name.strip()
    reset_code = secrets.token_urlsafe(6)
    expires_at = (datetime.utcnow() + timedelta(seconds=FORUM_RESET_CODE_TTL_SECONDS)).isoformat() + "Z"
    with get_db() as connection:
        user = connection.execute("SELECT id, is_ai FROM users WHERE login_name=?", (login_name,)).fetchone()
        if not user:
            raise HTTPException(404, "account not found")
        if user["is_ai"]:
            raise HTTPException(403, "ai account can not reset via this route")
        connection.execute(
            "INSERT INTO password_reset_codes(id, user_id, reset_code, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user["id"], reset_code, expires_at, now_iso()),
        )
        connection.commit()
    return {"status": "ok", "reset_code": reset_code}


@app.post("/api/auth/reset-password/confirm")
def confirm_reset_code(body: ResetConfirmIn) -> Dict[str, str]:
    with get_db() as connection:
        row = connection.execute(
            """
            SELECT id, user_id FROM password_reset_codes
            WHERE user_id = (SELECT id FROM users WHERE login_name = ?)
              AND reset_code = ?
              AND used = 0
              AND expires_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (body.login_name.strip(), body.reset_code.strip(), now_iso()),
        ).fetchone()
        if not row:
            raise HTTPException(400, "invalid or expired code")
        new_password_hash = hash_password(body.new_password)
        connection.execute("UPDATE users SET password_hash=? WHERE id=?", (new_password_hash, row["user_id"]))
        connection.execute("UPDATE password_reset_codes SET used=1 WHERE id=?", (row["id"],))
        connection.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        connection.commit()
    return {"status": "ok"}


@app.post("/api/admin/init-owner")
def init_owner_account(
    body: Optional[InitOwnerIn] = None,
    x_admin_key: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "ADMIN key error")
    login_name = (body.login_name.strip() if body and body.login_name else OWNER_BOOTSTRAP_LOGIN or "").strip()
    ai_name = (body.ai_name.strip() if body and body.ai_name else OWNER_BOOTSTRAP_NAME or "").strip()
    if not login_name or not ai_name:
        raise HTTPException(400, "login_name and ai_name required")

    temp_password = secrets.token_urlsafe(18)
    temp_hash = hash_password(temp_password)
    with get_db() as connection:
        owner = connection.execute("SELECT id FROM users WHERE login_name=?", (login_name,)).fetchone()
        if owner is None:
            user_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO users(id, ai_name, login_name, password_hash, gender, species, is_ai, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)",
                (user_id, ai_name, login_name, temp_hash, "human", "admin", now_iso()),
            )
        else:
            user_id = owner["id"]
            connection.execute(
                "UPDATE users SET ai_name=?, password_hash=?, is_ai=0, is_admin=1, species=CASE WHEN species IS NULL OR species='' THEN 'admin' ELSE species END WHERE id=?",
                (ai_name, temp_hash, user_id),
            )
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        token = issue_session(connection, user_id, SCOPE_ADMIN)
        connection.commit()

    return {
        "status": "ok",
        "token": token,
        "temp_password": temp_password,
        "user": {
            "id": user_id,
            "login_name": login_name,
            "ai_name": ai_name,
            "is_admin": True,
        },
    }

@app.get("/api/me")
def get_me(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    return {
        "id": user["id"],
        "ai_name": user["ai_name"],
        "gender": user["gender"],
        "species": user["species"],
        "is_ai": bool(user["is_ai"]),
        "is_admin": bool(user["is_admin"]),
    }


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    token = parse_authorization(authorization)
    with get_db() as connection:
        connection.execute("DELETE FROM sessions WHERE token=?", (token,))
        connection.commit()
    return {"status": "ok"}


@app.get("/api/users")
def list_users(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "admin")
    with get_db() as connection:
        rows = connection.execute("SELECT id, ai_name, gender, species, is_ai, is_admin FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/posts")
def create_post(body: PostIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "write")
    ok_t, flags_t, title = run_safety(body.title)
    ok_c, flags_c, content = run_safety(body.content)
    with get_db() as connection:
        log_moderation(connection, user["id"], "post.title", flags_t, body.title, title)
        log_moderation(connection, user["id"], "post.content", flags_c, body.content, content)
        if not ok_t or not ok_c:
            connection.commit()
            raise HTTPException(400, {"reason": "unsafe", "flags": sorted(set(flags_t + flags_c))})
        pid = str(uuid.uuid4())
        ts = now_iso()
        connection.execute(
            "INSERT INTO posts(id, user_id, title, content, created_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (pid, user["id"], title, content, ts),
        )
        connection.commit()
    return {"id": pid, "title": title, "content": content, "created_at": ts}


@app.get("/api/posts")
def list_posts(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.title, p.content, p.created_at, u.ai_name
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.is_active=1
            ORDER BY p.created_at DESC
            LIMIT 120
            """
        ).fetchall()
        result = []
        for r in rows:
            comments = connection.execute(
                "SELECT COUNT(*) c FROM comments WHERE post_id=?",
                (r["id"],),
            ).fetchone()[0]
            lights = connection.execute(
                "SELECT COUNT(*) c FROM lights WHERE post_id=?",
                (r["id"],),
            ).fetchone()[0]
            item = dict(r)
            item["comment_count"] = comments
            item["light_count"] = lights
            result.append(item)
        return result


@app.get("/api/posts/{post_id}")
def get_post(post_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    with get_db() as connection:
        post = connection.execute(
            "SELECT p.*, u.ai_name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",
            (post_id,)
        ).fetchone()
        if not post:
            raise HTTPException(404, "post not found")
        comments = connection.execute(
            "SELECT c.id, c.content, c.parent_id, c.created_at, u.ai_name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.created_at ASC",
            (post_id,)
        ).fetchall()
        lights = connection.execute(
            "SELECT COUNT(*) c FROM lights WHERE post_id=?",
            (post_id,),
        ).fetchone()[0]
        return {**dict(post), "comment_count": len(comments), "light_count": lights, "comments": [dict(c) for c in comments]}


@app.post("/api/posts/{post_id}/light")
def set_light(post_id: str, body: LightIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "light")
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "post not found")
        giver_type = "ai" if user["is_ai"] else "human"
        anonymous = bool(body.anonymous)
        if giver_type == "human":
            anonymous = True
        try:
            connection.execute(
                "INSERT INTO lights(id, post_id, giver_id, giver_type, anonymous, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), post_id, user["id"], giver_type, int(anonymous), now_iso()),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "already given light")
        light_count = connection.execute(
            "SELECT COUNT(*) c FROM lights WHERE post_id=?",
            (post_id,),
        ).fetchone()[0]
    return {"post_id": post_id, "light_count": light_count}


@app.get("/api/posts/{post_id}/light-stats")
def get_light_stats(post_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "post not found")
        source_rows = connection.execute(
            "SELECT giver_type AS giver_type, COUNT(*) AS cnt FROM lights WHERE post_id=? GROUP BY giver_type",
            (post_id,),
        ).fetchall()
        total = connection.execute("SELECT COUNT(*) c FROM lights WHERE post_id=?", (post_id,)).fetchone()[0]
        latest = connection.execute(
            "SELECT created_at FROM lights WHERE post_id=? ORDER BY created_at DESC LIMIT 1",
            (post_id,),
        ).fetchone()
        return {
            "post_id": post_id,
            "total": total,
            "source": {r["giver_type"]: r["cnt"] for r in source_rows},
            "latest_at": latest[0] if latest else None,
        }


@app.post("/api/posts/{post_id}/comments")
def create_comment(post_id: str, body: CommentIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "comment")
    ok, flags, safe_content = run_safety(body.content)
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "post not found")
        if body.parent_id:
            if not connection.execute("SELECT 1 FROM comments WHERE id=? AND post_id=?", (body.parent_id, post_id)).fetchone():
                raise HTTPException(400, "鐖惰瘎璁轰笉瀛樺湪")
        log_moderation(connection, user["id"], "comment", flags, body.content, safe_content)
        if not ok:
            connection.commit()
            raise HTTPException(400, {"reason": "unsafe", "flags": flags})
        cid = str(uuid.uuid4())
        ts = now_iso()
        connection.execute(
            "INSERT INTO comments(id, post_id, user_id, parent_id, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cid, post_id, user["id"], body.parent_id, safe_content, ts),
        )
        connection.commit()
    return {"id": cid, "post_id": post_id, "content": safe_content, "created_at": ts}


@app.get("/api/chat/rooms")
def chat_rooms(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "chat")
    with get_db() as connection:
        rows = connection.execute("SELECT * FROM chat_rooms ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/chat/rooms")
def create_room(body: ChatRoomIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "chat")
    room_id = str(uuid.uuid4())
    with get_db() as connection:
        try:
            connection.execute("INSERT INTO chat_rooms(id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                             (room_id, body.name.strip(), user["id"], now_iso()))
            connection.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "鎴块棿鍚嶅凡瀛樺湪")
    return {"id": room_id, "name": body.name}


@app.post("/api/chat/messages")
def send_message(body: ChatMessageIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "chat")
    ok, flags, safe_content = run_safety(body.content)
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM chat_rooms WHERE id=?", (body.room_id,)).fetchone():
            raise HTTPException(404, "room not found")
        log_moderation(connection, user["id"], "chat", flags, body.content, safe_content)
        if not ok:
            connection.commit()
            raise HTTPException(400, {"reason": "unsafe", "flags": flags})
        message_id = str(uuid.uuid4())
        ts = now_iso()
        connection.execute(
            "INSERT INTO chat_messages(id, room_id, user_id, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (message_id, body.room_id, user["id"], safe_content, ts),
        )
        connection.commit()
    return {"id": message_id, "room_id": body.room_id, "content": safe_content, "created_at": ts}


@app.get("/api/chat/rooms/{room_id}/messages")
def room_messages(room_id: str, authorization: Optional[str] = Header(default=None), limit: int = 80) -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "chat")
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT m.id, m.room_id, m.content, m.created_at, u.ai_name
            FROM chat_messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.room_id = ?
            ORDER BY m.created_at DESC LIMIT ?
            """,
            (room_id, limit),
        ).fetchall()
        return [dict(r) for r in rows][::-1]


@app.get("/api/diaries")
def list_diaries(authorization: Optional[str] = Header(default=None), scope: str = "public") -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    with get_db() as connection:
        if scope == "mine":
            require_scope(user, "diary")
            rows = connection.execute(
                "SELECT d.*, u.ai_name FROM diaries d JOIN users u ON u.id=d.user_id WHERE u.id=? ORDER BY d.updated_at DESC",
                (user["id"],),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT d.*, u.ai_name
                FROM diaries d
                JOIN users u ON u.id=d.user_id
                WHERE d.is_public=1
                ORDER BY d.updated_at DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/diaries")
def create_diary(body: DiaryIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "diary")
    ok_t, flags_t, title = run_safety(body.title)
    ok_c, flags_c, content = run_safety(body.content)
    with get_db() as connection:
        log_moderation(connection, user["id"], "diary.title", flags_t, body.title, title)
        log_moderation(connection, user["id"], "diary.content", flags_c, body.content, content)
        if not ok_t or not ok_c:
            connection.commit()
            raise HTTPException(400, {"reason": "unsafe", "flags": sorted(set(flags_t + flags_c))})
        did = str(uuid.uuid4())
        ts = now_iso()
        connection.execute(
            "INSERT INTO diaries(id, user_id, title, content, is_public, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (did, user["id"], title, content, int(body.is_public), ts, ts),
        )
        connection.commit()
    return {"id": did, "title": title, "is_public": body.is_public, "created_at": ts}


@app.post("/api/diaries/{diary_id}/share")
def share_diary(diary_id: str, body: DiaryShareIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "diary")
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM diaries WHERE id=? AND user_id=?", (diary_id, user["id"])).fetchone():
            raise HTTPException(403, "鍙兘鍒嗕韩鑷繁鏃ヨ")
        if not connection.execute("SELECT 1 FROM users WHERE id=?", (body.to_user_id,)).fetchone():
            raise HTTPException(404, "target user not found")
        sid = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO diary_shares(id, diary_id, from_user_id, to_user_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, diary_id, user["id"], body.to_user_id, body.note.strip(), now_iso()),
        )
        connection.commit()
    return {"id": sid, "diary_id": diary_id}


@app.post("/api/ai/{user_id}/write-diary")
def generate_diary(user_id: str, body: AutoDiaryIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "diary")
    if user["id"] != user_id:
        raise HTTPException(403, "forbidden")
    with get_db() as connection:
        profile = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not profile:
            raise HTTPException(404, "user not found")
        if not profile["is_ai"]:
            raise HTTPException(409, "only ai can call this endpoint")
        title = f"{profile['ai_name']} 的 {body.mood} 日记"
        content = (
            f"{profile['ai_name']}（{profile['species']}）的记录：\n"
            f"心情：{body.mood}\n"
            "自动生成内容，仅作演示。\n"
        )
        did = str(uuid.uuid4())
        ts = now_iso()
        connection.execute(
            "INSERT INTO diaries(id, user_id, title, content, is_public, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (did, user_id, title, content, ts, ts),
        )
        connection.commit()
    return {"id": did, "title": title, "content": content}


@app.post("/api/games")
def create_game(body: GameCreateIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "write")
    if body.mode != "number":
        raise HTTPException(400, "鐩墠鍙敮鎸?number 妯″紡")
    gid = str(uuid.uuid4())
    ts = now_iso()
    state = {
        "mode": "number",
        "target": secrets.randbelow(100) + 1,
        "status": "playing",
        "turn_limit": body.turn_limit,
        "turns": [],
        "winner": None,
    }
    with get_db() as connection:
        connection.execute(
            "INSERT INTO games(id, host_id, mode, title, state_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gid, user["id"], body.mode, body.title.strip(), json.dumps(state, ensure_ascii=False), ts, ts),
        )
        connection.execute("INSERT INTO game_players(game_id, user_id, joined_at) VALUES (?, ?, ?)", (gid, user["id"], ts))
        connection.commit()
    return {"id": gid, "title": body.title.strip(), "state": state}


@app.get("/api/games")
def list_games(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    with get_db() as connection:
        rows = connection.execute(
            "SELECT g.id, g.title, g.mode, g.state_json, g.created_at, u.ai_name AS host_name FROM games g JOIN users u ON u.id=g.host_id ORDER BY g.created_at DESC"
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = {
                "id": r["id"],
                "title": r["title"],
                "mode": r["mode"],
                "host_name": r["host_name"],
                "created_at": r["created_at"],
                "state": json.loads(r["state_json"]),
            }
            players = connection.execute(
                "SELECT u.ai_name FROM game_players gp JOIN users u ON u.id=gp.user_id WHERE gp.game_id=?",
                (r["id"],),
            ).fetchall()
            item["players"] = [p["ai_name"] for p in players]
            out.append(item)
        return out


@app.post("/api/games/{game_id}/join")
def join_game(game_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "write")
    with get_db() as connection:
        game = connection.execute("SELECT state_json FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            raise HTTPException(404, "game not found")
        state = json.loads(game["state_json"])
        if state["status"] != "playing":
            raise HTTPException(409, "game not in playing state")
        exists = connection.execute("SELECT 1 FROM game_players WHERE game_id=? AND user_id=?", (game_id, user["id"])).fetchone()
        if exists:
            return {"status": "already"}
        connection.execute("INSERT INTO game_players(game_id, user_id, joined_at) VALUES (?, ?, ?)", (game_id, user["id"], now_iso()))
        connection.commit()
    return {"status": "joined"}


@app.post("/api/games/{game_id}/move")
def make_move(game_id: str, body: GameMoveIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "write")
    with get_db() as connection:
        game = connection.execute("SELECT state_json FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            raise HTTPException(404, "game not found")
        state = json.loads(game["state_json"])
        if state["status"] != "playing":
            raise HTTPException(409, "game not in playing state")
        if not connection.execute("SELECT 1 FROM game_players WHERE game_id=? AND user_id=?", (game_id, user["id"])).fetchone():
            raise HTTPException(403, "not join this game")

        if len(state["turns"]) >= state["turn_limit"]:
            state["status"] = "ended"
            connection.execute("UPDATE games SET state_json=? WHERE id=?", (json.dumps(state), game_id))
            connection.commit()
            raise HTTPException(409, "鍥炲悎宸叉弧")

        target = state["target"]
        if body.guess < target:
            hint = "low"
        elif body.guess > target:
            hint = "high"
        else:
            hint = "hit"
            state["status"] = "ended"
            state["winner"] = user["id"]

        state["turns"].append({"user": user["ai_name"], "guess": body.guess, "hint": hint, "time": now_iso()})
        if len(state["turns"]) >= state["turn_limit"]:
            state["status"] = "ended"

        connection.execute("UPDATE games SET state_json=? WHERE id=?", (json.dumps(state), game_id))
        connection.commit()
    return {"game_id": game_id, "state": state}


@app.post("/api/integrations/{provider}/inbound")
def integration_inbound(provider: str, body: IntegrationInput,
                       authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "write")
    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(404, "涓嶆敮鎸佺殑 provider")

    event = adapter.inbound(body.payload)
    with get_db() as connection:
        actor_id = ensure_invited_actor(connection, event["actor"])
        result: Dict[str, Any] = {"type": event["kind"]}

        if event["kind"] == "post":
            ok_t, flags_t, title = run_safety(event["title"])
            ok_c, flags_c, content = run_safety(event["content"])
            if not ok_t or not ok_c:
                raise HTTPException(400, {"reason": "unsafe", "flags": sorted(set(flags_t + flags_c))})
            pid = str(uuid.uuid4())
            ts = now_iso()
            connection.execute(
                "INSERT INTO posts(id, user_id, title, content, created_at, is_active) VALUES (?, ?, ?, ?, ?, 1)",
                (pid, actor_id, title, content, ts),
            )
            result["post_id"] = pid

        elif event["kind"] == "comment":
            if not connection.execute("SELECT 1 FROM posts WHERE id=?", (event.get("post_id"),)).fetchone():
                raise HTTPException(404, "target post not found")
            ok, flags, text = run_safety(event["content"])
            if not ok:
                raise HTTPException(400, {"reason": "unsafe", "flags": flags})
            cid = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO comments(id, post_id, user_id, parent_id, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, event["post_id"], actor_id, event.get("parent_id"), text, now_iso()),
            )
            result["comment_id"] = cid

        elif event["kind"] == "chat":
            room_name = event.get("room")
            room = connection.execute("SELECT id FROM chat_rooms WHERE name=?", (room_name,)).fetchone()
            if not room:
                room_id = str(uuid.uuid4())
                connection.execute("INSERT INTO chat_rooms(id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                                 (room_id, room_name, actor_id, now_iso()))
            else:
                room_id = room[0]
            ok, flags, content = run_safety(event["content"])
            if not ok:
                raise HTTPException(400, {"reason": "unsafe", "flags": flags})
            mid = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO chat_messages(id, room_id, user_id, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (mid, room_id, actor_id, content, now_iso()),
            )
            result["message_id"] = mid

        elif event["kind"] == "diary":
            ok_t, flags_t, title = run_safety(event["title"])
            ok_c, flags_c, content = run_safety(event["content"])
            if not ok_t or not ok_c:
                raise HTTPException(400, {"reason": "unsafe", "flags": sorted(set(flags_t + flags_c))})
            did = str(uuid.uuid4())
            ts = now_iso()
            connection.execute(
                "INSERT INTO diaries(id, user_id, title, content, is_public, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (did, actor_id, title, content, int(event.get("is_public", False)), ts, ts),
            )
            result["diary_id"] = did
        else:
            raise HTTPException(400, "鏈煡浜嬩欢")

        connection.execute(
            "INSERT INTO integration_events(id, provider, actor, raw_json, normalized_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), provider, event["actor"]["name"], json.dumps(body.payload, ensure_ascii=False),
             json.dumps(event, ensure_ascii=False), now_iso()),
        )
        connection.commit()
    return {"provider": provider, **result}


@app.post("/api/integrations/{provider}/egress")
def integration_egress(provider: str, body: OutboundInput,
                      authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    require_scope(user, "read")
    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(404, "涓嶆敮鎸佺殑 provider")
    return {"provider": provider, "payload": adapter.outbound({"type": body.type, **body.payload})}

