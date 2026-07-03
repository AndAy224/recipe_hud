import base64
import binascii

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..settings_store import verify_password

security = HTTPBasic(auto_error=False)

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": 'Basic realm="Recipe HUD Admin"'}


def is_local(request: Request) -> bool:
    """The kiosk itself talks from localhost and is implicitly trusted."""
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1")


def check_basic_header(store, header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode()
        _, _, password = decoded.partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return verify_password(store.get("admin_password_hash"), password)


async def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    if is_local(request):
        return
    store = request.app.state.store
    if credentials and verify_password(store.get("admin_password_hash"), credentials.password):
        return
    raise HTTPException(status_code=401, detail="Admin authentication required",
                        headers=UNAUTHORIZED_HEADERS)
