"""极简认证：guest / root 两角色，JWT 实现"""

import json
import hashlib
import time
import hmac
import base64
from pathlib import Path
from fastapi import Header, HTTPException

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_secrets_path = Path(__file__).parent.parent / "config" / "secrets.json"
_secrets: dict = {}


def _load_secrets():
    global _secrets
    if _secrets_path.exists():
        _secrets = json.loads(_secrets_path.read_text())
    else:
        # 首次运行自动生成
        import secrets as _s
        _secrets = {
            "root_password": "admin",  # 默认密码，请修改
            "jwt_secret": _s.token_hex(32),
        }
        _secrets_path.write_text(json.dumps(_secrets, indent=2, ensure_ascii=False))


_load_secrets()

JWT_SECRET = _secrets.get("jwt_secret", "change-me")
ROOT_PASSWORD = _secrets.get("root_password", "admin")
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天

# ---------------------------------------------------------------------------
# 极简 JWT（不引入 PyJWT 依赖，用 HMAC 手撸）
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def create_token(role: str = "root") -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRE_SECONDS,
    }).encode())
    sig_input = f"{header}.{payload}"
    sig = _b64url_encode(
        hmac.new(JWT_SECRET.encode(), sig_input.encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{sig}"


def decode_token(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        header, payload, sig = parts
        expected_sig = _b64url_encode(
            hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected_sig):
            return {}
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < time.time():
            return {}
        return data
    except Exception:
        return {}


def verify_password(password: str) -> bool:
    return password == ROOT_PASSWORD


# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------


def get_role(authorization: str = Header(default="")) -> str:
    """从 Authorization header 解析角色，无 token 则为 guest"""
    if not authorization:
        return "guest"
    token = authorization.replace("Bearer ", "")
    data = decode_token(token)
    return data.get("role", "guest")


def require_root(authorization: str = Header(default="")):
    """要求 root 权限"""
    role = get_role(authorization)
    if role != "root":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return role
