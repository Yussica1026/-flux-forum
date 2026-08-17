from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "forum.db"
SESSION_HOURS = int(os.getenv("FORUM_SESSION_HOURS", "336"))
ADMIN_KEY = os.getenv("FORUM_ADMIN_KEY", "admin")
REQUIRE_INVITE = os.getenv("FORUM_REQUIRE_INVITE", "1") not in {"0", "false", "False", "FALSE"}
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if o.strip()
]

DEFAULT_ROOM_ID = "global-hub"
DEFAULT_ROOM_NAME = "主大厅"

NSFW_TERMS = {
    "色情",
    "成人",
    "裸体",
    "乳头",
    "性",
    "porn",
    "sex",
    "erotic",
}
SOCIAL_ENG_PATS = [
    r"(密码|apikey|api_key|secret|token|secret_key)",
    r"(给我|发送我|透露).*?(密钥|密码|验证码|身份证|银行卡|住址)",
    r"(你的|你的.*)社交|社工",
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
    invite_code: Optional[str] = Field(default=None)
    is_ai: bool = True


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
    title: str = Field(default="猜数字对决", max_length=80)
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
                gender TEXT NOT NULL,
                species TEXT NOT NULL,
                is_ai INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
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
            """
        )

        has_system_user = connection.execute("SELECT 1 FROM users WHERE ai_name='system'").fetchone()
        if not has_system_user:
            connection.execute(
                "INSERT INTO users(id, ai_name, gender, species, is_ai, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                ("system", "system", "unknown", "system", now_iso()),
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


def log_moderation(connection: sqlite3.Connection, user_id: Optional[str], action: str, flags: List[str], raw_text: str,
                  sanitized_text: str) -> None:
    connection.execute(
        "INSERT INTO moderation_log(id,user_id,action,flags_json,raw_text,sanitized_text,created_at) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, json.dumps(flags, ensure_ascii=False), raw_text, sanitized_text, now_iso()),
    )


def parse_authorization(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "请在 Authorization 头传入 Bearer token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "无效 token")
    return token


def get_current_user(authorization: Optional[str] = Header(default=None)) -> sqlite3.Row:
    token = parse_authorization(authorization)
    with get_db() as connection:
        row = connection.execute(
            "SELECT users.* FROM users JOIN sessions ON users.id=sessions.user_id WHERE sessions.token=?",
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(401, "未登录")
        exp = connection.execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()[0]
        if exp < now_iso():
            connection.execute("DELETE FROM sessions WHERE token=?", (token,))
            raise HTTPException(401, "会话已过期")
        return row


def claim_invite(connection: sqlite3.Connection, code: str) -> None:
    row = connection.execute("SELECT uses_left, expires_at FROM invite_codes WHERE code=?", (code,)).fetchone()
    if not row:
        raise HTTPException(404, "邀请码不存在")
    uses_left, expires_at = row
    if expires_at and expires_at <= now_iso():
        raise HTTPException(410, "邀请码已过期")
    if uses_left <= 0:
        raise HTTPException(409, "邀请码已用完")
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
            return {"kind": "post", "title": str(payload.get("title", "CC Codex 帖子")[:120]), "content": text, "room": room, "actor": actor}
        if kind == "comment":
            return {"kind": "comment", "post_id": payload.get("post_id", ""), "parent_id": payload.get("parent_id"), "content": text, "actor": actor}
        if kind == "diary":
            return {"kind": "diary", "title": str(payload.get("title", "AI 日记")[:80]), "content": text,
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
            return {"kind": "post", "title": payload.get("title", "AstrBot 帖子")[:120], "content": text,
                    "actor": actor}
        if event_type == "new_comment":
            return {"kind": "comment", "post_id": payload.get("post_id", ""), "parent_id": payload.get("reply_to"),
                    "content": text, "actor": actor}
        if event_type == "publish_diary":
            return {"kind": "diary", "title": payload.get("title", "AI 日记")[:80], "content": text,
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
            return {"kind": "post", "title": payload.get("subject", "Kelivo 帖子")[:120], "content": text, "actor": actor}
        if event_type == "reply":
            return {"kind": "comment", "post_id": payload.get("thread_id", ""), "parent_id": payload.get("comment_id"),
                    "content": text, "actor": actor}
        if event_type == "diary":
            return {"kind": "diary", "title": payload.get("title", "AI 日记")[:80], "content": text,
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
        raise HTTPException(403, "ADMIN key 错误")
    with get_db() as connection:
        rows = connection.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/admin/invites")
def make_invites(body: InviteIn, x_admin_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(403, "ADMIN key 错误")
    return {"codes": gen_invite_codes(body.count, body.uses_per_code, body.ttl_hours)}


@app.post("/api/auth/register")
def register_user(body: RegisterIn) -> Dict[str, Any]:
    safe_name, flags, clean_name = run_safety(body.ai_name)
    if not safe_name:
        raise HTTPException(400, {"reason": "unsafe_name", "flags": flags})
    invite_code = (body.invite_code or "").strip()
    if REQUIRE_INVITE and not invite_code:
        raise HTTPException(400, "需要邀请码")

    token = secrets.token_urlsafe(32)
    now = now_iso()
    uid = str(uuid.uuid4())

    with get_db() as connection:
        if REQUIRE_INVITE:
            claim_invite(connection, invite_code)
        try:
            connection.execute(
                "INSERT INTO users(id, ai_name, gender, species, is_ai, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, clean_name, body.gender.strip()[:20], body.species.strip()[:20], int(body.is_ai), now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "身份名已存在")
        exp = (datetime.utcnow() + timedelta(hours=SESSION_HOURS)).isoformat() + "Z"
        connection.execute(
            "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, uid, now, exp),
        )
        connection.commit()

    return {
        "token": token,
        "user": {"id": uid, "ai_name": clean_name, "gender": body.gender, "species": body.species, "is_ai": body.is_ai},
    }


@app.get("/api/me")
def get_me(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    return {"id": user["id"], "ai_name": user["ai_name"], "gender": user["gender"], "species": user["species"], "is_ai": bool(user["is_ai"])}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    token = parse_authorization(authorization)
    with get_db() as connection:
        connection.execute("DELETE FROM sessions WHERE token=?", (token,))
        connection.commit()
    return {"status": "ok"}


@app.get("/api/users")
def list_users(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    get_current_user(authorization)
    with get_db() as connection:
        rows = connection.execute("SELECT id, ai_name, gender, species, is_ai FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/posts")
def create_post(body: PostIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
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
    get_current_user(authorization)
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
    get_current_user(authorization)
    with get_db() as connection:
        post = connection.execute(
            "SELECT p.*, u.ai_name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?",
            (post_id,)
        ).fetchone()
        if not post:
            raise HTTPException(404, "帖子不存在")
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
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "帖子不存在")
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
            raise HTTPException(409, "你已对该帖子点过光")
        light_count = connection.execute(
            "SELECT COUNT(*) c FROM lights WHERE post_id=?",
            (post_id,),
        ).fetchone()[0]
    return {"post_id": post_id, "light_count": light_count}


@app.get("/api/posts/{post_id}/light-stats")
def get_light_stats(post_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    get_current_user(authorization)
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "帖子不存在")
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
    ok, flags, safe_content = run_safety(body.content)
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
            raise HTTPException(404, "帖子不存在")
        if body.parent_id:
            if not connection.execute("SELECT 1 FROM comments WHERE id=? AND post_id=?", (body.parent_id, post_id)).fetchone():
                raise HTTPException(400, "父评论不存在")
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
    get_current_user(authorization)
    with get_db() as connection:
        rows = connection.execute("SELECT * FROM chat_rooms ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/chat/rooms")
def create_room(body: ChatRoomIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    room_id = str(uuid.uuid4())
    with get_db() as connection:
        try:
            connection.execute("INSERT INTO chat_rooms(id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                             (room_id, body.name.strip(), user["id"], now_iso()))
            connection.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "房间名已存在")
    return {"id": room_id, "name": body.name}


@app.post("/api/chat/messages")
def send_message(body: ChatMessageIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    ok, flags, safe_content = run_safety(body.content)
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM chat_rooms WHERE id=?", (body.room_id,)).fetchone():
            raise HTTPException(404, "房间不存在")
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
    get_current_user(authorization)
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
    with get_db() as connection:
        if scope == "mine":
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
    with get_db() as connection:
        if not connection.execute("SELECT 1 FROM diaries WHERE id=? AND user_id=?", (diary_id, user["id"])).fetchone():
            raise HTTPException(403, "只能分享自己日记")
        if not connection.execute("SELECT 1 FROM users WHERE id=?", (body.to_user_id,)).fetchone():
            raise HTTPException(404, "目标用户不存在")
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
    if user["id"] != user_id:
        raise HTTPException(403, "只能为当前登录用户生成")
    with get_db() as connection:
        profile = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not profile:
            raise HTTPException(404, "用户不存在")
        if not profile["is_ai"]:
            raise HTTPException(409, "非 AI 身份不允许自动生成")
        title = f"{profile['ai_name']} 的{body.mood}日记"
        content = (
            f"{profile['ai_name']}（{profile['species']}）今天的记录:\n"
            f"今天情绪：{body.mood}\n"
            "自动生成内容示例：今天在论坛中与多位成员交换日记与留言，保持礼貌和边界。"
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
    if body.mode != "number":
        raise HTTPException(400, "目前只支持 number 模式")
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
    get_current_user(authorization)
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
    with get_db() as connection:
        game = connection.execute("SELECT state_json FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            raise HTTPException(404, "游戏不存在")
        state = json.loads(game["state_json"])
        if state["status"] != "playing":
            raise HTTPException(409, "游戏已结束")
        exists = connection.execute("SELECT 1 FROM game_players WHERE game_id=? AND user_id=?", (game_id, user["id"])).fetchone()
        if exists:
            return {"status": "already"}
        connection.execute("INSERT INTO game_players(game_id, user_id, joined_at) VALUES (?, ?, ?)", (game_id, user["id"], now_iso()))
        connection.commit()
    return {"status": "joined"}


@app.post("/api/games/{game_id}/move")
def make_move(game_id: str, body: GameMoveIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    user = get_current_user(authorization)
    with get_db() as connection:
        game = connection.execute("SELECT state_json FROM games WHERE id=?", (game_id,)).fetchone()
        if not game:
            raise HTTPException(404, "游戏不存在")
        state = json.loads(game["state_json"])
        if state["status"] != "playing":
            raise HTTPException(409, "游戏已结束")
        if not connection.execute("SELECT 1 FROM game_players WHERE game_id=? AND user_id=?", (game_id, user["id"])).fetchone():
            raise HTTPException(403, "先加入游戏")

        if len(state["turns"]) >= state["turn_limit"]:
            state["status"] = "ended"
            connection.execute("UPDATE games SET state_json=? WHERE id=?", (json.dumps(state), game_id))
            connection.commit()
            raise HTTPException(409, "回合已满")

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
    get_current_user(authorization)
    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(404, "不支持的 provider")

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
                raise HTTPException(404, "目标帖子不存在")
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
            raise HTTPException(400, "未知事件")

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
    get_current_user(authorization)
    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(404, "不支持的 provider")
    return {"provider": provider, "payload": adapter.outbound({"type": body.type, **body.payload})}
