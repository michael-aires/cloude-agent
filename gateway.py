"""
iOS Gateway API for Cloude Agent.

Provides mobile-optimized endpoints for the Cloude Agent iOS application.
Mounts as a sub-router on the main FastAPI app under /api/v1/.

Features:
- JWT-based device authentication
- Conversation management (CRUD + message history)
- SSE streaming with mobile-friendly event format
- Push notification (APNS) device token registration
- Artifact browsing and preview metadata
- Agent capability discovery
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# JWT-like token signing key. In production, set via environment variable.
_GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")
_TOKEN_TTL_SECONDS = int(os.environ.get("GATEWAY_TOKEN_TTL", str(60 * 60 * 24 * 30)))  # 30 days default
_REFRESH_TTL_SECONDS = int(os.environ.get("GATEWAY_REFRESH_TTL", str(60 * 60 * 24 * 90)))  # 90 days

# ---------------------------------------------------------------------------
# Simple token utilities (HMAC-based, no external JWT library needed)
# ---------------------------------------------------------------------------


def _get_secret() -> str:
    """Return the signing secret, falling back to API_KEY if GATEWAY_SECRET is not set."""
    return _GATEWAY_SECRET or os.environ.get("API_KEY", "insecure-dev-key")


def _sign_token(payload: dict, ttl: int) -> str:
    """Create a signed token embedding *payload* with an expiry."""
    payload = {**payload, "exp": int(time.time()) + ttl, "iat": int(time.time())}
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    import base64

    b64 = base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")
    sig = hmac.new(_get_secret().encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def _verify_token(token: str) -> Optional[dict]:
    """Verify and decode a signed token. Returns payload or None."""
    import base64

    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    b64, sig = parts
    expected = hmac.new(_get_secret().encode(), b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    # Restore padding
    padded = b64 + "=" * (-len(b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(..., description="Unique device identifier (UUID from iOS)")
    device_name: Optional[str] = Field(default=None, description="Human-readable device name")
    platform: str = Field(default="ios", description="Platform identifier")
    app_version: Optional[str] = Field(default=None, description="App build version")
    api_key: str = Field(..., description="API key for initial authentication")


class DeviceRegisterResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token from registration")


class APNSTokenRequest(BaseModel):
    apns_token: str = Field(..., description="APNS device token (hex string)")
    environment: str = Field(default="production", description="APNS environment (sandbox or production)")


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="Conversation title (auto-generated if omitted)")
    model: Optional[str] = Field(default=None, description="Model override for this conversation")


class ConversationUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


class MessageRequest(BaseModel):
    message: str = Field(..., description="User message text")
    command: Optional[str] = Field(default=None, description="Slash command to invoke")
    images: Optional[list[dict]] = Field(default=None, description="Base64-encoded images [{data, media_type}]")
    model: Optional[str] = Field(default=None, description="Model override for this message")


class ConversationSummary(BaseModel):
    id: str
    title: str
    last_message_preview: str = ""
    last_active: Optional[str] = None
    created: Optional[str] = None
    message_count: int = 0
    pinned: bool = False
    archived: bool = False
    model: Optional[str] = None


class MessageEntry(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None
    tools_used: Optional[list[str]] = None
    model: Optional[str] = None


class ArtifactInfo(BaseModel):
    path: str
    name: str
    size: int
    modified: str
    type: str  # html, pdf, markdown, image, code, other
    preview_url: str


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("API_KEY", "")


async def verify_gateway_token(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Verify authentication via:
    1. Bearer token (JWT-like signed token from device registration)
    2. Fallback to X-API-Key header (backwards compatible)
    """
    # Try Bearer token first
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = _verify_token(token)
        if payload:
            return payload
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Fallback to API key
    if x_api_key and x_api_key == _API_KEY:
        return {"device_id": "api-key-user", "type": "api_key"}

    raise HTTPException(status_code=401, detail="Authentication required")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["iOS Gateway"])


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/register", response_model=DeviceRegisterResponse)
async def register_device(req: DeviceRegisterRequest, request: Request):
    """
    Register an iOS device and receive access + refresh tokens.
    Requires a valid API key for initial bootstrap.
    """
    if not req.api_key or req.api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    payload = {
        "device_id": req.device_id,
        "device_name": req.device_name or req.device_id[:8],
        "platform": req.platform,
        "type": "access",
    }
    access_token = _sign_token(payload, _TOKEN_TTL_SECONDS)
    refresh_payload = {**payload, "type": "refresh", "nonce": secrets.token_hex(8)}
    refresh_token = _sign_token(refresh_payload, _REFRESH_TTL_SECONDS)

    # Store device info in Redis if agent_manager is available
    from main import agent_manager

    if agent_manager and agent_manager.redis:
        device_data = {
            "device_id": req.device_id,
            "device_name": req.device_name,
            "platform": req.platform,
            "app_version": req.app_version,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        await agent_manager.redis.set(
            f"device:{req.device_id}",
            json.dumps(device_data),
            ex=_REFRESH_TTL_SECONDS,
        )

    return DeviceRegisterResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=_TOKEN_TTL_SECONDS,
        device_id=req.device_id,
    )


@router.post("/auth/refresh")
async def refresh_token(req: TokenRefreshRequest):
    """Refresh an expired access token using a valid refresh token."""
    payload = _verify_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_payload = {
        "device_id": payload["device_id"],
        "device_name": payload.get("device_name", ""),
        "platform": payload.get("platform", "ios"),
        "type": "access",
    }
    new_access = _sign_token(access_payload, _TOKEN_TTL_SECONDS)

    return {
        "access_token": new_access,
        "expires_in": _TOKEN_TTL_SECONDS,
        "device_id": payload["device_id"],
    }


@router.post("/auth/apns")
async def register_apns_token(
    req: APNSTokenRequest,
    auth: dict = Depends(verify_gateway_token),
):
    """Register an APNS device token for push notifications."""
    from main import agent_manager

    device_id = auth.get("device_id", "unknown")

    if agent_manager and agent_manager.redis:
        await agent_manager.redis.set(
            f"apns:{device_id}",
            json.dumps({
                "token": req.apns_token,
                "environment": req.environment,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }),
            ex=_REFRESH_TTL_SECONDS,
        )

    return {"status": "registered", "device_id": device_id}


# ---------------------------------------------------------------------------
# Conversations (enhanced session management)
# ---------------------------------------------------------------------------


def _redis_conv_key(conv_id: str) -> str:
    return f"conversation:{conv_id}"


def _redis_conv_meta_key(device_id: str) -> str:
    return f"conversations:{device_id}"


async def _get_redis():
    from main import agent_manager

    if not agent_manager or not agent_manager.redis:
        raise HTTPException(status_code=503, detail="Service not ready")
    return agent_manager.redis


async def _get_agent_manager():
    from main import agent_manager

    if not agent_manager:
        raise HTTPException(status_code=503, detail="Service not ready")
    return agent_manager


@router.get("/conversations")
async def list_conversations(
    auth: dict = Depends(verify_gateway_token),
    limit: int = 50,
    offset: int = 0,
    archived: bool = False,
):
    """List conversations for the authenticated device, newest first."""
    r = await _get_redis()
    device_id = auth.get("device_id", "unknown")

    # Get conversation index for this device
    raw = await r.get(_redis_conv_meta_key(device_id))
    conv_ids: list[str] = json.loads(raw) if raw else []

    conversations: list[dict] = []
    for cid in conv_ids:
        raw_conv = await r.get(_redis_conv_key(cid))
        if not raw_conv:
            continue
        conv = json.loads(raw_conv)
        if conv.get("archived", False) != archived:
            continue
        conversations.append(conv)

    # Sort by last_active descending
    conversations.sort(key=lambda c: c.get("last_active", ""), reverse=True)

    # Pinned first
    pinned = [c for c in conversations if c.get("pinned")]
    unpinned = [c for c in conversations if not c.get("pinned")]
    conversations = pinned + unpinned

    total = len(conversations)
    page = conversations[offset : offset + limit]

    return {
        "conversations": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/conversations")
async def create_conversation(
    req: ConversationCreateRequest,
    auth: dict = Depends(verify_gateway_token),
):
    """Create a new conversation."""
    r = await _get_redis()
    device_id = auth.get("device_id", "unknown")

    conv_id = f"conv-{secrets.token_hex(8)}"
    now = datetime.now(timezone.utc).isoformat()
    conv = {
        "id": conv_id,
        "title": req.title or "New Conversation",
        "created": now,
        "last_active": now,
        "last_message_preview": "",
        "message_count": 0,
        "pinned": False,
        "archived": False,
        "model": req.model,
        "device_id": device_id,
    }

    await r.set(_redis_conv_key(conv_id), json.dumps(conv), ex=60 * 60 * 24 * 90)

    # Add to device's conversation index
    raw = await r.get(_redis_conv_meta_key(device_id))
    conv_ids: list[str] = json.loads(raw) if raw else []
    conv_ids.insert(0, conv_id)
    # Keep max 200 conversations
    conv_ids = conv_ids[:200]
    await r.set(_redis_conv_meta_key(device_id), json.dumps(conv_ids), ex=60 * 60 * 24 * 90)

    return conv


@router.get("/conversations/{conv_id}")
async def get_conversation(
    conv_id: str,
    auth: dict = Depends(verify_gateway_token),
    include_messages: bool = True,
):
    """Get conversation details with optional message history."""
    r = await _get_redis()
    am = await _get_agent_manager()

    raw = await r.get(_redis_conv_key(conv_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = json.loads(raw)

    if include_messages:
        history = await am._get_conversation_history(conv_id)
        conv["messages"] = history

    return conv


@router.patch("/conversations/{conv_id}")
async def update_conversation(
    conv_id: str,
    req: ConversationUpdateRequest,
    auth: dict = Depends(verify_gateway_token),
):
    """Update conversation metadata (title, pin, archive)."""
    r = await _get_redis()

    raw = await r.get(_redis_conv_key(conv_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = json.loads(raw)

    if req.title is not None:
        conv["title"] = req.title
    if req.pinned is not None:
        conv["pinned"] = req.pinned
    if req.archived is not None:
        conv["archived"] = req.archived

    await r.set(_redis_conv_key(conv_id), json.dumps(conv), ex=60 * 60 * 24 * 90)

    return conv


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    auth: dict = Depends(verify_gateway_token),
):
    """Delete a conversation and its history."""
    r = await _get_redis()
    device_id = auth.get("device_id", "unknown")

    await r.delete(_redis_conv_key(conv_id))
    await r.delete(f"history:{conv_id}")

    # Remove from device index
    raw = await r.get(_redis_conv_meta_key(device_id))
    conv_ids: list[str] = json.loads(raw) if raw else []
    conv_ids = [cid for cid in conv_ids if cid != conv_id]
    await r.set(_redis_conv_meta_key(device_id), json.dumps(conv_ids), ex=60 * 60 * 24 * 90)

    return {"status": "deleted", "id": conv_id}


# ---------------------------------------------------------------------------
# Messages (chat within a conversation)
# ---------------------------------------------------------------------------


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: str,
    req: MessageRequest,
    auth: dict = Depends(verify_gateway_token),
):
    """Send a message in a conversation (non-streaming). Returns the full response."""
    r = await _get_redis()
    am = await _get_agent_manager()

    # Ensure conversation exists
    raw = await r.get(_redis_conv_key(conv_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = json.loads(raw)

    # Build context
    device_id = auth.get("device_id", "unknown")
    device_name = auth.get("device_name", "iOS User")
    context = {
        "source": "ios-app",
        "user_name": device_name,
        "permission_mode": "acceptEdits",
    }

    images = None
    if req.images:
        images = [{"data": img.get("data", ""), "media_type": img.get("media_type", "image/jpeg")} for img in req.images]

    result = await am.chat(
        user_session_id=conv_id,
        message=req.message if not req.command else f"/{req.command} {req.message}",
        images=images,
        context=context,
        model=req.model or conv.get("model"),
    )

    # Update conversation metadata
    now = datetime.now(timezone.utc).isoformat()
    preview = result.get("response", "")[:100]
    conv["last_active"] = now
    conv["last_message_preview"] = preview
    conv["message_count"] = conv.get("message_count", 0) + 2  # user + assistant
    if conv["title"] == "New Conversation" and req.message:
        conv["title"] = req.message[:60] + ("..." if len(req.message) > 60 else "")
    await r.set(_redis_conv_key(conv_id), json.dumps(conv), ex=60 * 60 * 24 * 90)

    return {
        "conversation_id": conv_id,
        "response": result.get("response", ""),
        "tools_used": result.get("tools_used", []),
        "usage": result.get("usage", {}),
        "model": req.model or conv.get("model"),
    }


@router.post("/conversations/{conv_id}/messages/stream")
async def stream_message(
    conv_id: str,
    req: MessageRequest,
    request: Request,
    auth: dict = Depends(verify_gateway_token),
):
    """Send a message and stream the response via SSE."""
    r = await _get_redis()
    am = await _get_agent_manager()

    raw = await r.get(_redis_conv_key(conv_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = json.loads(raw)

    device_id = auth.get("device_id", "unknown")
    device_name = auth.get("device_name", "iOS User")
    context = {
        "source": "ios-app",
        "user_name": device_name,
        "permission_mode": "acceptEdits",
    }

    images = None
    if req.images:
        images = [{"data": img.get("data", ""), "media_type": img.get("media_type", "image/jpeg")} for img in req.images]

    message = req.message if not req.command else f"/{req.command} {req.message}"

    async def event_generator():
        full_response = ""
        tools_used = []
        try:
            async for event in am.chat_stream(
                user_session_id=conv_id,
                message=message,
                images=images,
                context=context,
                model=req.model or conv.get("model"),
            ):
                # Track for conversation update
                if event.get("type") == "text":
                    full_response += event.get("text", "")
                elif event.get("type") == "tool" and event.get("status") == "started":
                    tools_used.append(event.get("name", ""))

                yield f"data: {json.dumps(event)}\n\n"

                # Update conversation on done
                if event.get("type") == "done":
                    now = datetime.now(timezone.utc).isoformat()
                    conv["last_active"] = now
                    conv["last_message_preview"] = full_response[:100]
                    conv["message_count"] = conv.get("message_count", 0) + 2
                    if conv["title"] == "New Conversation" and req.message:
                        conv["title"] = req.message[:60] + ("..." if len(req.message) > 60 else "")
                    await r.set(_redis_conv_key(conv_id), json.dumps(conv), ex=60 * 60 * 24 * 90)

        except Exception as e:
            logger.exception("Gateway stream error for conversation %s", conv_id)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/conversations/{conv_id}/interrupt")
async def interrupt_conversation(
    conv_id: str,
    auth: dict = Depends(verify_gateway_token),
):
    """Interrupt an active streaming response in a conversation."""
    am = await _get_agent_manager()
    interrupted = await am.interrupt_stream(conv_id)
    if not interrupted:
        raise HTTPException(status_code=404, detail="No active stream for this conversation")
    return {"status": "interrupted", "conversation_id": conv_id}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _classify_file_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".html", ".htm")):
        return "html"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".md", ".markdown")):
        return "markdown"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico")):
        return "image"
    if lower.endswith((".json", ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".css",
                       ".yaml", ".yml", ".toml", ".xml", ".sql", ".csv", ".txt",
                       ".log", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".h",
                       ".swift", ".kt")):
        return "code"
    return "other"


@router.get("/artifacts")
async def list_artifacts(
    auth: dict = Depends(verify_gateway_token),
    path: str = "",
):
    """List artifacts with type classification and preview URLs."""
    from main import ARTIFACTS_DIR

    artifacts_root = Path(ARTIFACTS_DIR).resolve()
    target = (artifacts_root / path).resolve()

    try:
        target.relative_to(artifacts_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.exists():
        return {"path": path, "items": []}

    if target.is_file():
        stat = target.stat()
        rel = str(target.relative_to(artifacts_root))
        return {
            "path": path,
            "items": [
                {
                    "path": rel,
                    "name": target.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "type": _classify_file_type(target.name),
                    "is_directory": False,
                }
            ],
        }

    items = []
    for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        try:
            stat = entry.stat()
        except OSError:
            continue
        rel = str(entry.relative_to(artifacts_root))
        items.append(
            {
                "path": rel,
                "name": entry.name,
                "size": stat.st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "type": _classify_file_type(entry.name) if entry.is_file() else "directory",
                "is_directory": entry.is_dir(),
            }
        )

    return {"path": path, "items": items}


# ---------------------------------------------------------------------------
# Agent info & capabilities
# ---------------------------------------------------------------------------


@router.get("/agent/info")
async def agent_info(auth: dict = Depends(verify_gateway_token)):
    """Get agent capabilities, available skills, models, and commands."""
    am = await _get_agent_manager()

    skills = am.list_skills()
    commands = am.list_commands()
    from main import _get_model_defaults

    defaults, default_model_id, default_alias = _get_model_defaults()

    return {
        "name": "Cloude Agent",
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "images": True,
            "artifacts": True,
            "commands": True,
            "skills": True,
            "canvas": True,
            "voice": bool(os.environ.get("OPENAI_API_KEY")),
            "github_cli": True,
        },
        "skills": [
            {"id": s["id"], "name": s["name"], "description": s.get("description", "")}
            for s in skills
        ],
        "commands": [
            {"id": c["id"], "name": c.get("name", c["id"]), "description": c.get("description", "")}
            for c in commands
        ],
        "models": {
            "defaults": defaults,
            "default_model_id": default_model_id,
            "default_alias": default_alias,
        },
    }


@router.get("/agent/skills")
async def list_skills(auth: dict = Depends(verify_gateway_token)):
    """List available agent skills with full descriptions."""
    am = await _get_agent_manager()
    return {"skills": am.list_skills()}


@router.get("/agent/commands")
async def list_commands(auth: dict = Depends(verify_gateway_token)):
    """List available slash commands."""
    am = await _get_agent_manager()
    return {"commands": am.list_commands()}


# ---------------------------------------------------------------------------
# Settings (per-device preferences)
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_settings(auth: dict = Depends(verify_gateway_token)):
    """Get user/device settings."""
    r = await _get_redis()
    device_id = auth.get("device_id", "unknown")

    raw = await r.get(f"settings:{device_id}")
    settings = json.loads(raw) if raw else {}

    from main import _get_model_defaults

    defaults, default_model_id, default_alias = _get_model_defaults()

    return {
        "device_id": device_id,
        "preferences": {
            "model": settings.get("model"),
            "notifications_enabled": settings.get("notifications_enabled", True),
            "haptic_feedback": settings.get("haptic_feedback", True),
            "auto_title": settings.get("auto_title", True),
            "stream_responses": settings.get("stream_responses", True),
        },
        "server_defaults": {
            "model": default_model_id,
            "model_alias": default_alias,
        },
    }


@router.put("/settings")
async def update_settings(
    request: Request,
    auth: dict = Depends(verify_gateway_token),
):
    """Update user/device settings."""
    r = await _get_redis()
    device_id = auth.get("device_id", "unknown")

    body = await request.json()
    allowed_keys = {"model", "notifications_enabled", "haptic_feedback", "auto_title", "stream_responses"}
    updates = {k: v for k, v in body.items() if k in allowed_keys}

    raw = await r.get(f"settings:{device_id}")
    settings = json.loads(raw) if raw else {}
    settings.update(updates)

    await r.set(f"settings:{device_id}", json.dumps(settings), ex=60 * 60 * 24 * 365)

    return {"status": "updated", "preferences": settings}


# ---------------------------------------------------------------------------
# Health (mobile-specific)
# ---------------------------------------------------------------------------


@router.get("/health")
async def gateway_health():
    """Gateway health check (no auth)."""
    from main import agent_manager

    redis_ok = False
    if agent_manager and agent_manager.redis:
        try:
            await agent_manager.redis.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "status": "ok",
        "gateway": "v1",
        "redis": "connected" if redis_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
