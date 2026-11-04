"""Google OIDC authorization-code login. No calendar access or browser token storage."""
import base64
import hashlib
import logging
import secrets
from urllib.parse import urlencode

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from database import get_db, User, LoginSession, OAuthFlow
from security import identity, Identity, digest, now, rate_limit, mutation_limit

router = APIRouter(prefix="/api/auth", tags=["authentication"])
logger = logging.getLogger(__name__)


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 10
        return super().__call__(*args, **kwargs)


def verify_google_token(token):
    with requests.Session() as transport:
        return id_token.verify_oauth2_token(token, BoundedGoogleRequest(session=transport), config.GOOGLE_CLIENT_ID)


def user_json(user):
    return {"id": user.id, "name": user.name, "email": user.email}


@router.get("/config")
def auth_config():
    return {"google_enabled": bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET), "ai_enabled": config.AI_ENABLED}


@router.get("/google")
def google_start(request: Request, db: Session = Depends(get_db)):
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google sign-in is not configured")
    # Uses socket address, never an untrusted X-Forwarded-For header.
    rate_limit(db, "login:" + (request.client.host if request.client else "unknown"), 20, 60)
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    db.add(OAuthFlow(state_hash=digest(state), browser_hash=digest(browser), nonce=nonce, verifier=verifier, expires_at=now() + 600))
    db.commit()
    params = {"client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": config.APP_URL + "/api/auth/google/callback", "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="), "code_challenge_method": "S256", "prompt": "select_account"}
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=303)
    response.set_cookie(config.FLOW_COOKIE, browser, max_age=600, httponly=True, secure=config.PRODUCTION, samesite="lax", path="/")
    return response


@router.get("/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    flow = db.get(OAuthFlow, digest(state)) if state and len(state) < 256 else None
    browser = request.cookies.get(config.FLOW_COOKIE, "")
    if not flow or flow.expires_at <= now() or not secrets.compare_digest(flow.browser_hash, digest(browser)):
        raise HTTPException(400, "Login expired or invalid; please sign in again")
    # A conditional delete consumes the flow exactly once, even on concurrent callbacks.
    consumed = db.execute(delete(OAuthFlow).where(OAuthFlow.state_hash == flow.state_hash)).rowcount
    db.commit()
    if not consumed or error or not code or len(code) > 4096:
        raise HTTPException(400, "Sign-in cancelled or expired; please try again")
    try:
        result = httpx.post("https://oauth2.googleapis.com/token", data={"client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET, "code": code, "code_verifier": flow.verifier, "grant_type": "authorization_code", "redirect_uri": config.APP_URL + "/api/auth/google/callback"}, timeout=15)
        result.raise_for_status()
        claims = verify_google_token(result.json()["id_token"])
        if not secrets.compare_digest(str(claims.get("nonce", "")), flow.nonce) or claims.get("email_verified") is not True:
            raise ValueError("Invalid identity")
        sub = str(claims["sub"])
        email = str(claims["email"]).strip().lower()
        if not sub or len(sub) > 255 or len(email) > 254:
            raise ValueError("Invalid identity")
    except Exception:
        # Never expose provider responses, codes, tokens, or credentials.
        logger.warning("Google sign-in verification failed")
        return RedirectResponse("/?auth_error=1", status_code=303)
    user = db.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        # Never link accounts by email: the verified provider subject is the identity.
        user = User(google_sub=sub, email=email, name=str(claims.get("name") or email.split("@")[0])[:120])
        db.add(user)
    else:
        user.email = email
    try:
        db.flush()
        raw_token = secrets.token_urlsafe(32)
        previous = request.cookies.get(config.SESSION_COOKIE)
        if previous:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(previous)))
        db.add(LoginSession(token_hash=digest(raw_token), user_id=user.id, csrf=secrets.token_urlsafe(32), expires_at=now() + config.SESSION_SECONDS))
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/?auth_error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(config.SESSION_COOKIE, raw_token, max_age=config.SESSION_SECONDS, httponly=True, secure=config.PRODUCTION, samesite="lax", path="/")
    response.delete_cookie(config.FLOW_COOKIE, path="/", secure=config.PRODUCTION, httponly=True, samesite="lax")
    return response


@router.get("/me")
def me(auth: Identity = Depends(identity)):
    return {"user": user_json(auth.user), "csrf_token": auth.session.csrf}


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")


@router.patch("/me")
def update_profile(body: ProfileUpdate, auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    mutation_limit(db, auth.user.id)
    auth.user.name = body.name.strip()
    db.commit()
    return user_json(auth.user)


@router.post("/logout", status_code=204)
def logout(auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    db.delete(auth.session)
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(config.SESSION_COOKIE, path="/", secure=config.PRODUCTION, httponly=True, samesite="lax")
    return response


@router.post("/logout-all", status_code=204)
def logout_all(auth: Identity = Depends(identity), db: Session = Depends(get_db)):
    db.execute(delete(LoginSession).where(LoginSession.user_id == auth.user.id))
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(config.SESSION_COOKIE, path="/", secure=config.PRODUCTION, httponly=True, samesite="lax")
    return response
