"""Sign-in with the company's provider (hosted mode, docs/hosted-design.md "Sign-in").

showntell is an OIDC client of Google, Entra or any OIDC provider. It runs the authorization code
flow with PKCE, validates the ID token (signature from the provider's JWKS, issuer, audience,
nonce, expiry) with joserfc, then applies the preset's rule for who may sign in. Provider tokens
never leave this module: a signed-in user gets a showntell browser session, and MCP clients get
showntell's own tokens (oauth.py).

| Preset | Issuer | Who may sign in |
|---|---|---|
| google | https://accounts.google.com | the `hd` claim is an allowed domain (an @gmail.com account has none) |
| entra | https://login.microsoftonline.com/<tenant>/v2.0 | `tid` is the tenant, then the UPN's domain if domains are listed |
| generic | SHOWNTELL_OIDC_ISSUER | `email_verified` is true and the email's domain is allowed |

Entra's `email` claim is never read: it is editable and unverified. The rule runs at every sign-in
and again at every refresh of a showntell token, on the claims kept from the last sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from .identity import User

log = logging.getLogger("showntell.oidc")

PRESETS = ("google", "entra", "generic")
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
JWKS_TTL_S = 3600.0
LEEWAY_S = 60


class SignInRefused(Exception):
    """The provider vouched for someone who may not use this server, or its answer did not check out.
    The message is shown to the person signing in."""


def issuer_for(preset: str, tenant: str = "", issuer: str = "") -> str:
    if preset == "google":
        return "https://accounts.google.com"
    if preset == "entra":
        return f"https://login.microsoftonline.com/{tenant}/v2.0"
    return issuer.rstrip("/")


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1].lower() if "@" in (address or "") else ""


def check_claims(preset: str, claims: dict, domains: list[str], tenant: str = "", guests: bool = False) -> str:
    """The address a user is known by (verified email or UPN, lower case), if the preset's rule
    lets these claims sign in; raises SignInRefused otherwise. Entra B2B guests sit in the tenant
    too, so they are refused unless `guests`: Entra marks them with an `idp` other than the issuer."""
    domains = [d.lower() for d in domains]
    if preset == "google":
        hd = (claims.get("hd") or "").lower()
        if not hd:
            raise SignInRefused("Sign in with your company's Google account, not a personal one.")
        if hd not in domains:
            raise SignInRefused(f"Accounts from {hd} cannot use this server.")
        if claims.get("email_verified") is not True or _domain(claims.get("email", "")) != hd:
            raise SignInRefused("Your Google account's email is not verified for your company's domain.")
        return claims["email"].lower()
    if preset == "entra":
        if (claims.get("tid") or "").lower() != tenant.lower():
            raise SignInRefused("This account belongs to another organisation.")
        upn = (claims.get("preferred_username") or "").lower()  # the UPN; `email` is never trusted
        if domains and _domain(upn) not in domains:
            raise SignInRefused(f"Accounts from {_domain(upn) or 'that directory'} cannot use this server.")
        if not upn:
            raise SignInRefused("The sign-in did not say who you are (no preferred_username).")
        idp = (claims.get("idp") or "").rstrip("/")
        if not guests and ((idp and idp != (claims.get("iss") or "").rstrip("/")) or "#ext#" in upn):
            raise SignInRefused("Guest accounts from other organisations cannot use this server.")
        return upn
    if claims.get("email_verified") is not True:
        raise SignInRefused("Your email address is not verified with the sign-in provider.")
    email = (claims.get("email") or "").lower()
    if _domain(email) not in domains:
        raise SignInRefused(f"Accounts from {_domain(email) or 'that provider'} cannot use this server.")
    return email


KEPT_CLAIMS = ("iss", "sub", "email", "email_verified", "hd", "tid", "preferred_username", "idp")


@dataclass
class Provider:
    """One configured provider: discovery, the login redirect, the code exchange and ID token checks."""

    preset: str
    client_id: str
    client_secret: str
    issuer: str
    redirect_uri: str  # <public url>/oidc/callback
    domains: list[str] = field(default_factory=list)
    tenant: str = ""
    guests: bool = False  # entra: admit B2B guests
    http: httpx.AsyncClient | None = None  # tests pass one; otherwise made on first use
    _meta: dict | None = None
    _jwks: object = None
    _jwks_at: float = 0.0

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=10, follow_redirects=False)
        return self.http

    async def metadata(self) -> dict:
        """The provider's discovery document; its issuer must be the configured one."""
        if self._meta is None:
            r = await self._client().get(self.issuer + "/.well-known/openid-configuration")
            r.raise_for_status()
            meta = r.json()
            if meta.get("issuer", "").rstrip("/") != self.issuer:
                raise SignInRefused(f"the provider's discovery document names issuer {meta.get('issuer')!r}, not {self.issuer!r}")
            if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
                raise SignInRefused("the provider does not support PKCE with S256")
            self._meta = meta
        return self._meta

    async def keys(self, refresh: bool = False):
        from joserfc.jwk import KeySet

        if self._jwks is None or refresh or time.time() - self._jwks_at > JWKS_TTL_S:
            r = await self._client().get((await self.metadata())["jwks_uri"])
            r.raise_for_status()
            self._jwks, self._jwks_at = KeySet.import_key_set(r.json()), time.time()
        return self._jwks

    async def login_url(self, state: str, nonce: str, verifier: str) -> str:
        meta = await self.metadata()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        q = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self.preset == "google" and len(self.domains) == 1:
            q["hd"] = self.domains[0]  # a hint for the account chooser; the hd claim is what is checked
        return meta["authorization_endpoint"] + ("&" if "?" in meta["authorization_endpoint"] else "?") + urlencode(q)

    async def finish(self, code: str, verifier: str, nonce: str) -> tuple[User, dict]:
        """Exchange the code, validate the ID token and apply the preset's rule. Returns the user
        and the claims kept for the checks at refresh."""
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        meta = await self.metadata()
        methods = meta.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        auth = "client_secret_post" if "client_secret_post" in methods and "client_secret_basic" not in methods else "client_secret_basic"
        async with AsyncOAuth2Client(
            self.client_id, self.client_secret, token_endpoint_auth_method=auth, redirect_uri=self.redirect_uri, timeout=10
        ) as oauth:
            try:
                token = await oauth.fetch_token(meta["token_endpoint"], code=code, code_verifier=verifier, grant_type="authorization_code")
            except Exception as e:  # the provider refused the code: an old link or a replay
                raise SignInRefused(f"the provider did not accept the sign-in ({type(e).__name__})") from None
        id_token = token.get("id_token")
        if not id_token:
            raise SignInRefused("the provider returned no ID token")
        claims = await self.validate(id_token, nonce)
        email = check_claims(self.preset, claims, self.domains, self.tenant, self.guests)
        return User(claims["iss"], claims["sub"], email), {k: claims[k] for k in KEPT_CLAIMS if k in claims}

    async def validate(self, id_token: str, nonce: str) -> dict:
        """Signature (the provider's JWKS, refetched once for an unknown key), issuer, audience,
        expiry, issued-at and nonce."""
        from joserfc import jwt
        from joserfc.errors import JoseError

        try:
            try:
                token = jwt.decode(id_token, await self.keys(), algorithms=["RS256", "ES256", "PS256"])
            except (JoseError, ValueError):
                token = jwt.decode(id_token, await self.keys(refresh=True), algorithms=["RS256", "ES256", "PS256"])
            registry = jwt.JWTClaimsRegistry(
                leeway=LEEWAY_S,
                iss={"essential": True, "value": self.issuer},
                aud={"essential": True, "value": self.client_id},
                sub={"essential": True},
                exp={"essential": True},
                iat={"essential": True},
                nonce={"essential": True, "value": nonce},
            )
            registry.validate(token.claims)
        except (JoseError, ValueError) as e:
            raise SignInRefused(f"the ID token did not check out ({type(e).__name__})") from None
        claims = dict(token.claims)
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.client_id:
            raise SignInRefused("the ID token was issued to another app")
        return claims


def new_login() -> dict:
    """State, nonce and PKCE verifier for one sign-in."""
    return {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": secrets.token_urlsafe(48)}
