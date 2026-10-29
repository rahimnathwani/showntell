"""A fake OIDC provider for the sign-in tests: discovery, JWKS, an authorize endpoint that signs in
whoever the test names, and a token endpoint that checks the client secret and PKCE and returns a
signed ID token. Tests change `claims` for the next sign-in and `tamper` to break the ID token."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import secrets
import time
from urllib.parse import urlencode

import uvicorn
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

CLIENT_ID = "showntell-test-client"
CLIENT_SECRET = "fake-provider-secret"


class FakeOIDC:
    def __init__(self, port: int, issuer_path: str = "") -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.issuer = self.base + issuer_path
        self.port = port
        self.key = RSAKey.generate_key(2048, parameters={"kid": "good", "alg": "RS256", "use": "sig"})
        self.other_key = RSAKey.generate_key(2048, parameters={"kid": "good", "alg": "RS256", "use": "sig"})
        self.claims: dict = {}  # who the next sign-in is
        self.tamper: dict = {}  # claims to override in the ID token; "_key": "other" signs with a key not in the JWKS
        self.codes: dict[str, dict] = {}
        self.issued: list[str] = []  # every ID token handed out

    def app(self) -> Starlette:
        path = self.issuer[len(self.base) :]

        async def discovery(request: Request):
            return JSONResponse(
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.base}/authorize",
                    "token_endpoint": f"{self.base}/token",
                    "jwks_uri": f"{self.base}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
                }
            )

        async def jwks(request: Request):
            return JSONResponse(KeySet([self.key]).as_dict(private=False))

        async def authorize(request: Request):
            q = request.query_params
            assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256" and "openid" in q["scope"].split()
            code = secrets.token_urlsafe(16)
            self.codes[code] = {
                "nonce": q["nonce"],
                "challenge": q["code_challenge"],
                "redirect_uri": q["redirect_uri"],
                "claims": dict(self.claims),
            }
            return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q['state']})}", status_code=302)

        async def token(request: Request):
            form = await request.form()
            auth = request.headers.get("authorization", "")
            if auth.startswith("Basic "):
                cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            else:
                cid, secret = form.get("client_id"), form.get("client_secret")
            if cid != CLIENT_ID or secret != CLIENT_SECRET:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            grant = self.codes.pop(form.get("code", ""), None)
            if grant is None or form.get("redirect_uri") != grant["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            want = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
            if want != grant["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
            now = int(time.time())
            claims = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 600, "nonce": grant["nonce"], **grant["claims"]}
            tamper = dict(self.tamper)
            key = self.other_key if tamper.pop("_key", "") == "other" else self.key
            claims.update(tamper)
            id_token = jwt.encode({"alg": "RS256", "kid": "good"}, claims, key)
            self.issued.append(id_token)
            return JSONResponse({"access_token": "provider-access-token", "token_type": "Bearer", "expires_in": 600, "id_token": id_token})

        return Starlette(
            routes=[
                Route(f"{path}/.well-known/openid-configuration", discovery),
                Route("/jwks", jwks),
                Route("/authorize", authorize),
                Route("/token", token, methods=["POST"]),
            ]
        )

    @contextlib.asynccontextmanager
    async def running(self):
        server = uvicorn.Server(uvicorn.Config(self.app(), host="127.0.0.1", port=self.port, log_level="warning", lifespan="off"))
        task = asyncio.create_task(server.serve())
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        try:
            yield self
        finally:
            server.should_exit = True
            await task
