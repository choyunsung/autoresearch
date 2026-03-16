"""Simple authentication with JWT tokens and bcrypt password hashing."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import hashlib
import hmac
import secrets
import base64
import json

from fastapi import Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from collab.database import get_db
from collab.models import Researcher

# Persist secret key so tokens survive restarts
_SECRET_FILE = Path(__file__).parent / ".secret_key"
if _SECRET_FILE.exists():
  SECRET_KEY = _SECRET_FILE.read_text().strip()
else:
  SECRET_KEY = secrets.token_hex(32)
  _SECRET_FILE.write_text(SECRET_KEY)
  _SECRET_FILE.chmod(0o600)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
  salt = secrets.token_hex(16)
  h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
  return f"{salt}:{h.hex()}"


def verify_password(password: str, hashed: str) -> bool:
  salt, h = hashed.split(":")
  h2 = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
  return hmac.compare_digest(h, h2.hex())


def create_token(data: dict) -> str:
  payload = data.copy()
  expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
  payload["exp"] = expire.isoformat()
  # Simple HMAC-based token
  payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
  sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
  return f"{payload_b64}.{sig}"


def decode_token(token: str) -> Optional[dict]:
  try:
    payload_b64, sig = token.rsplit(".", 1)
    expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
      return None
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    exp = datetime.fromisoformat(payload["exp"])
    if datetime.now(timezone.utc) > exp:
      return None
    return payload
  except Exception:
    return None


def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)) -> Optional[Researcher]:
  """Get current user from session cookie (for web UI)."""
  token = request.cookies.get("access_token")
  if not token:
    return None
  data = decode_token(token)
  if not data:
    return None
  return db.query(Researcher).filter(Researcher.id == data["sub"]).first()


def require_user(request: Request, db: Session = Depends(get_db)) -> Researcher:
  """Require authenticated user (for protected endpoints)."""
  user = get_current_user_from_cookie(request, db)
  if not user:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
  return user


def require_user_web(request: Request, db: Session = Depends(get_db)) -> Researcher:
  """Require authenticated user for web routes — redirects to login instead of 401."""
  user = get_current_user_from_cookie(request, db)
  if not user:
    raise _LoginRequired()
  return user


class _LoginRequired(Exception):
  """Raised when a web page requires login."""
  pass


def get_current_user_api(
  credentials: HTTPAuthorizationCredentials = Depends(security),
  db: Session = Depends(get_db),
) -> Researcher:
  """Get current user from Bearer token (for API calls / CLI sync)."""
  if not credentials:
    raise HTTPException(status_code=401, detail="Missing token")
  data = decode_token(credentials.credentials)
  if not data:
    raise HTTPException(status_code=401, detail="Invalid token")
  user = db.query(Researcher).filter(Researcher.id == data["sub"]).first()
  if not user:
    raise HTTPException(status_code=401, detail="User not found")
  return user
