# Hosted mode: design

Status: phases 1 and 2 built on branch `hosted-mode`, 2026-10-28 and 2026-10-29 (see **Phase 1 as built** and **Phase 2 as built**). Not yet tried against real Google or Entra.

One showntell server for a whole company. Anyone at the company adds it to Claude (claude.ai connector, Claude Code, Desktop) by URL and signs in with the company's Google or Entra account. Walkthroughs belong to the person who made them; others see one only when the owner shares it.

## Goals and non-goals

| | |
|---|---|
| **Goals** | Sign-in with Google, Entra or any OIDC provider; an email-domain allowlist; MCP authorization as the current spec defines it; owner-only walkthroughs with explicit shares; no access to the server's disk or network from slide content; fair use of speech and Chromium across users; one Docker image |
| **Non-goals (v1)** | Admin console, groups or SCIM, per-user processes, embedding players on other sites, uploading images, billing |

No API keys: speech, layout checks and export stay on the server. The secrets are the OIDC client ID and secret, plus a signing key the server generates on first run and keeps in its data folder.

## Configuration

Hosted mode is `SHOWNTELL_MODE=hosted`. Everything else about it is off unless that is set, and the local daemon behaves as today.

| Variable | Meaning |
|---|---|
| `SHOWNTELL_PUBLIC_URL` | Required. The HTTPS origin users add, e.g. `https://showntell.example.com`; the MCP endpoint is `<url>/mcp` |
| `SHOWNTELL_OIDC` | `google`, `entra` or `generic` |
| `SHOWNTELL_OIDC_CLIENT_ID`, `SHOWNTELL_OIDC_CLIENT_SECRET` | From the provider's app registration |
| `SHOWNTELL_OIDC_ISSUER` | `generic` only; `entra` derives it from the tenant |
| `SHOWNTELL_ENTRA_TENANT` | `entra` only: the tenant ID (a GUID, not a domain) |
| `SHOWNTELL_ALLOWED_DOMAINS` | Comma-separated. Required for `google` and `generic`. Empty for `entra` means anyone in the tenant |
| `SHOWNTELL_QUOTA_*` | Per-user limits, below |
| `SHOWNTELL_DEV_USERS` | Trials on one machine only: comma-separated emails for the stub login, instead of `SHOWNTELL_OIDC` |

The server refuses to start in hosted mode with any of these missing or with a `http://` public URL other than localhost. It also refuses `SHOWNTELL_DELIVER=file` (a hosted server delivers links), both `SHOWNTELL_OIDC` and `SHOWNTELL_DEV_USERS` at once, an Entra tenant that is not a GUID, a generic issuer that is not HTTPS, and sign-in without the `hosted` extra (Authlib and joserfc). With neither OIDC nor the stub configured it refuses with a message naming both.

## Sign-in

showntell is both the MCP resource server and its own OAuth authorization server. It delegates the login itself to the company's provider and issues its own tokens. Google and Entra tokens never reach `/mcp`: accepting them would let any app holding one call showntell (the spec forbids this token passthrough).

```
MCP client ──/mcp, no token──▶ 401 + WWW-Authenticate: resource_metadata
           ──discovery──────▶ /.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server
           ──register───────▶ client ID metadata document, or dynamic registration
           ──/authorize (PKCE S256)──▶ showntell ──no session: OIDC──▶ Google / Entra ──▶ /oidc/callback
                                        checks identity and domain, records the user
                                        consent page: names the client and where it returns
           ◀──code + iss──  ──/token──▶ showntell access + refresh tokens (audience = <url>/mcp)
```

- **Spec version:** implement the latest published MCP authorization spec (check modelcontextprotocol.io at build time) and the negotiated protocol version on `/mcp`. Support client ID metadata documents and dynamic client registration both, since clients differ.
- **Tokens:** opaque random strings, stored hashed. Access tokens last an hour, refresh tokens 30 days and rotate on use; reuse of a spent refresh token revokes the chain. Every token records its audience (the resource parameter) and is refused elsewhere.
- **Browser sessions:** the viewer pages use the same OIDC login with a cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, 12 hours. State-changing POSTs also need a CSRF token.
- **Library:** use the MCP Python SDK's authorization server building blocks where they fit, and Authlib or similar for the OIDC client side. Don't hand-roll JWT validation.

### Identity and domain checks

The user's key is `(issuer, sub)`. Email is for display and for matching shares, never for identity.

| Check | Google | Entra | Generic OIDC |
|---|---|---|---|
| **Signature, issuer, audience, nonce, expiry** | Yes | Yes, issuer for the one tenant | Yes |
| **Who may sign in** | `hd` claim in `ALLOWED_DOMAINS` (an `@gmail.com` account has none) | `tid` equals `ENTRA_TENANT`; then, if domains are set, the domain of `preferred_username` (the UPN) is in the list | `email_verified` is true and the email's domain is in the list |
| **Never trust** | The email suffix without `hd` | The `email` claim: it is editable and unverified ("nOAuth") | — |
| **App registration** | Internal OAuth client in the company's Google Cloud project | Single-tenant app registration | Per the provider |

The domain check runs at every sign-in and every refresh, so removing a domain from the list takes effect within the access token's hour.

## Ownership and sharing

Every walkthrough has an `owner` (the user key) and a `shares` list. Access is checked on each request, so removing a share takes effect at once, including for audio, assets and the event stream.

| Action | Owner | Shared with | Anyone else |
|---|---|---|---|
| View page, `data.json`, audio, assets, poster, events | Yes | Yes | 404 |
| Player issue reports (`/w/{id}/report`) | Yes | Yes | 404 |
| `add_slides`, `revise_slide`, `finish_walkthrough`, `export_walkthrough`, delete | Yes | No | 404 |
| Appears in `list_walkthroughs` and the gallery page | Yes, as "yours" | Yes, as "shared with you" | No |

- **Share targets:** a person, by email address in an allowed domain; or the whole company (everyone who passes sign-in). Both are explicit, per walkthrough, and the default is no shares.
- **Matching:** a share to an email matches a user whose verified email (Google, generic) or UPN (Entra) equals it, case-insensitive. A share can name someone who hasn't signed in yet.
- **Where shares are made:** a new MCP tool `share_walkthrough(walkthrough, add=[...], remove=[...], company=true|false)`, and a Share control on the owner's view of the page. Its description is agent-facing text, in `text/hosted/tools.json`; the phase 1 build session wrote it, as its brief allowed, having read none of the outside reference material.
- **404, not 403:** nothing tells a non-owner that an ID exists. IDs stay 16 random bytes.
- **Exports:** MP4 and folder exports go to the owner only. An exported folder plays offline without sign-in, so sharing it is the owner's act, outside the server's control. `export_walkthrough` writes into the walkthrough's own folder (`exports/<slug>.mp4` or `.html`, one per format, overwritten) and replies with `<url>/w/{id}/exports/<name>`, served to the owner only, as an attachment. A `path` is refused. Exports count toward the owner's storage.
- **The Markdown resource:** `showntell://walkthroughs/{id}` is owner-only, like the write tools: a shared viewer watches, but does not get the source. The static `showntell://walkthroughs` list resource is not offered in hosted mode, because the SDK gives a static resource no caller; `list_walkthroughs` does the same job.
- **Routes that go:** `/api/rpc/*` (the stdio shim's route) and embedding with `Access-Control-Allow-Origin: *` are off in hosted mode.

## Slide content is untrusted

An agent can be steered by what it reads, so its Markdown is treated as hostile.

| Risk | Today | Hosted mode |
|---|---|---|
| **Script in a slide** | SVG rebuilt from an allowlist; draw.io parsing skips scripts; the player escapes text | The same, plus a CSP on every response: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:`, with `nosniff`, `Referrer-Policy: same-origin` and `X-Frame-Options: DENY`. The player's two inline scripts and the gallery's are files now (`static/boot.js`, `static/gallery.js`) in both modes. Neither Mermaid nor the draw.io viewer needs `unsafe-eval` (see **Phase 1 as built**) |
| **Reading the server's disk** | Code, diffs, images and git are read under `project_dir` | `project_dir`, file and git sources and local image paths are refused with a reply that says to put code and diffs in the Markdown. The wording lives in `text/hosted/` (instructions, tool and argument overrides, replies, and a note put before the guide); local text is unchanged |
| **Server-side requests (SSRF)** | Remote image URLs are allowed; the checks and export Chromium loads them | Remote images refused when slides are added (`img-src` blocks them anyway). The checks and export Chromium routes every request through an allowlist of showntell's own origin and aborts the rest. The container's outbound traffic is limited to the OIDC provider and the voice model download |
| **Hostile Markdown at parse time** | 1 MB per walkthrough | The same, plus 64 KB per `add_slides` or `revise_slide` call, checked before parsing. No parser timeout: parsing is CPU-bound Python on the event loop, which a timeout cannot interrupt, and a thread cannot be killed. Instead the parser is kept near-linear and a test builds a corpus of pathological inputs at the cap in under 2 s each (see **Phase 1 as built**) |

Later, optionally: serve walkthrough content from a second origin (`SHOWNTELL_CONTENT_URL`) so that a hole in the player can't use the main origin's session. It needs a sign-in handoff between origins, so it is left out of v1.

## Fair use

| Limit | Default |
|---|---|
| **Narration per walkthrough** | 8 minutes; past it the call is refused and the walkthrough left as it was |
| **Slides per walkthrough** | 60, refused the same way |
| **Markdown per call** | 64 KB |
| **Walkthroughs in progress per user** | 3; "in progress" is not finished and changed in the last hour, so an abandoned one stops counting |
| **Stored per user** | 200 walkthroughs or 2 GB |
| **Retention** | 180 days after last view, then deleted |
| **Speech** | One queue, served round-robin across users, so one long walkthrough doesn't hold up others |
| **Chromium** | 2 pages at once server-wide, 60 s per check, 15 min per export |
| **Requests** | 60 tool calls a minute per user, a sliding window; a start with its opening counts once |

Each is `SHOWNTELL_QUOTA_<NAME>`: `NARRATION_MIN`, `SLIDES`, `MARKDOWN_KB`, `WRITING`, `WALKTHROUGHS`, `STORAGE_GB`, `RETENTION_DAYS` (0 keeps forever), `CALLS_PER_MIN`, `BROWSERS`, `CHECK_S`, `EXPORT_MIN`. Storage counts a walkthrough's folder (state, source, exports) plus the audio files it plays; audio is shared by content, so two users voicing the same sentence each count it. Count and storage limits are checked at `start_walkthrough`. Retention runs daily: a walkthrough neither changed nor viewed for that long is deleted with `showntell prune`'s logic, which now also counts the last view.

## Process

- **One process for everyone:** user input is never run as code; Chromium has its own sandbox. Per-user processes would load the voice model and Chromium once each.
- **Container:** non-root user, read-only root filesystem, the data folder as the only writable volume, all capabilities dropped, Chromium sandbox on (no `--no-sandbox`), and Docker's default seccomp profile plus the four calls the sandbox needs (see **Phase 3 as built**).
- **TLS:** a reverse proxy in front; the compose file ships Caddy (self-hosted, automatic certificates).
- **Logs:** user key, walkthrough ID, tool, timing. No narration, code or tokens.

## Reachability

claude.ai connectors call the server from Anthropic's cloud, so for claude.ai it must be reachable from the internet. Claude Code and Desktop connect from the user's machine and work with an intranet-only server. The sign-in in front of everything is what makes the public option acceptable.

## Tests

| Test | Passes when |
|---|---|
| **Two users** | B gets 404 on every tool and route for A's walkthrough; after A shares, B can view but not change; after A removes the share, B gets 404 again, events stream included |
| **Sign-in** | A Google account with the wrong `hd`, no `hd`, an Entra token from another tenant, an Entra user outside the listed domains and an unverified generic email are each refused; an Entra token presented at `/mcp` is refused |
| **Tokens** | Expired, wrong-audience and spent refresh tokens are refused; reusing a refresh token revokes the chain |
| **Hostile Markdown** | A corpus (script tags, event handlers, `javascript:` links, SVG `foreignObject` and `use href`, mermaid and draw.io payloads) runs no script in Chromium |
| **Disk and network** | `../` paths, absolute paths, `project_dir` and git sources are refused; an image at `http://169.254.169.254/` is refused and never fetched |
| **Headers** | Every page carries the CSP; cookies have their flags; POSTs without the CSRF token fail |
| **Fair use** | Two users' speech interleaves; limits return clear errors |

CI runs these against a fake OIDC provider. One manual run per provider (Google, Entra) before release, adding the server as a claude.ai connector and in Claude Code.

## Phases

1. **Hosted switch:** the mode, ownership checks, no disk or remote fetches, CSP, quotas, speech queue. Testable with a stub login.
2. **Sign-in:** OIDC for the viewer pages, MCP authorization for `/mcp`, the domain checks, `share_walkthrough`.
3. **Packaging:** Dockerfile, compose file with Caddy, `docs/self-hosting.md` with the Google and Entra registration steps, a `showntell doctor` row for hosted settings.

## Phase 1 as built

What phase 1 decided where the design was open, and where it changed the design.

### The identity seam and the stub login

- **One seam:** `identity.Identity` turns a request into a `User(issuer, sub, email)`: `from_bearer` for `/mcp`, `from_cookie` for pages. It holds a `Login` (where users come from: `user_for_token` and its sign-in routes) and `BrowserSessions` (cookie sessions, each with its CSRF token, 12 hours, in memory). Phase 2 adds a provider `Login` and OAuth token checks behind the same calls; `access.py`, the store and the routes see only `User`.
- **Where the user travels:** `HostedGuard` puts the user in the ASGI scope; routes read it with `hosted.user_of(request)`, tools from the SDK's request context. The service and store take `user=` and check it; local mode passes nothing and checks nothing.
- **The stub:** `SHOWNTELL_DEV_USERS=a@example.com,b@example.com` gives each email a random bearer token, kept in `dev-logins.json` (0600) so trials survive restarts. MCP clients send `Authorization: Bearer <token>`; a browser signs in at `/dev/login?token=...`. It starts only with a loopback `SHOWNTELL_HOST` and a localhost public URL, so it cannot sit behind a public proxy, and every start logs a banner saying it is on and must never be used in production. As a backstop, while it is on the guard refuses (403) every request from a non-loopback peer or carrying `X-Forwarded-For`, `Forwarded` or `X-Real-IP`, so a proxy put in front of it on the same machine gets nothing. Its issuer is `showntell-dev-login`, so a stub user is never the same key as a real one.
- **Cookies on `/mcp`:** refused. `/mcp` takes only bearer tokens, so a page cannot drive tools with a viewer's cookie.

### Ownership

- **Fields:** `owner` (`{iss, sub, email}`), `shares` (lower-case emails), `company` (bool) and `viewed` live in `state.json`. `data.json` never carries them.
- **Unknown handles:** a write tool given someone else's handle gets the same "unknown handle" as a made-up one. The handle's key is still checked, so knowing an ID is not enough even for the owner.
- **Live updates:** the event stream checks access again on every event and keepalive; sharing changes push an event, so removing a share or deleting the walkthrough ends open streams within moments.
- **Walkthroughs without an owner** (a data folder reused from local mode) are visible to nobody in hosted mode.
- **`share_walkthrough` is in phase 1:** it fell out of the data model. Emails must be in `SHOWNTELL_ALLOWED_DOMAINS` when that is set; the owner's own email is ignored. The Share control on the page came in phase 2.

### Disk, network and Chromium

- **Builder:** in hosted mode `file=`, `rev=`, `svg file=`, `drawio file=` and every image (path or URL) become a `source_error` or `image_error` finding with a fix that says to paste the content into the Markdown; nothing is read or fetched. `project_dir` and an export `path` are refused outright. MCP roots are ignored, since they name folders on the client's machine.
- **The server's Chromium:** the finish check and preview open `http://<bind address>:<port>/w/{id}/` with a short browser session for the owner, through the guard; MP4 export opens its own loopback copy as before. All three route every request through a handler that aborts any other origin and logs it. A test points a slide's image at another local server and checks it gets no request.
- **Chromium limits:** an asyncio semaphore of `BROWSERS` held for each whole job; MP4 export uses one browser in hosted mode instead of up to four. Check and preview time out at `CHECK_S`, export at `EXPORT_MIN` (the reply returns; the render thread finishes in the background).

### Headers and CSRF

- **`style-src 'unsafe-inline'` (a design change):** Mermaid writes a `<style>` element into each diagram, KaTeX sets `style` attributes, and the page carries the theme's colours as a `style` attribute. Nonces cannot cover styles added by script. Inline style cannot run script, and with `img-src`, `font-src` and `connect-src` all `'self'`, CSS cannot send data anywhere else.
- **No `unsafe-eval`:** Mermaid's two `Function("return this")` calls are lodash's fallback for finding the global object, reached only where `self` is missing, never in a browser. The draw.io viewer's `mxUtils.eval` runs only to decode function-valued XML, sits in a try/catch, and under the CSP fails harmlessly; the hosted browser test draws Mermaid, KaTeX, code and draw.io slides with no CSP violation at all.
- **What the CSP catches:** with the hostile corpus, Mermaid adds one inline event handler while drawing a hostile label; the CSP blocks it. The same corpus in local mode, without a CSP, ran no script either, so this is defence in depth, not a hole.
- **CSRF:** every POST from a page needs the session's token in `X-CSRF-Token` (the page carries it as `data-csrf`), JSON, an Origin that is the public or internal origin when present, and a `Sec-Fetch-Site` of `same-origin`. Unauthenticated pages get 401; `/static/`, `/health` and the dev login are open. `Cache-Control` on audio is `private` in hosted mode.

### Fixes found on the way (both modes)

- **Nested SVG:** a `svg` block nested a few thousand levels deep raised `RecursionError` and failed the call; drawings now stop at 100 levels with a finding.
- **Unclosed emphasis:** 64 KB of `_a _a …` took 10 s to parse, because each `_` scanned to the end of the paragraph for its closer; emphasis spans now stop at 400 characters (0.16 s).
- **draw.io:** a compressed page may expand to at most 20 MB.

### Speech

- **Round-robin:** the narrator's single priority queue became `FairQueue`: one priority queue per user (owner key), served in turn. With one user, as in local mode, it is the same priority queue as before.

### Left for later

- **An owner index:** listings and limit checks read every `state.json`. Fine for hundreds of walkthroughs; an in-memory index by owner is the fix when it is not.
- **Admins, uploads, retention per company:** as under **Open questions**.

## Phase 2 as built

### MCP authorization

- **Spec:** the MCP authorization specification of 2026-07-28, the latest published when built. Dynamic client registration is deprecated there in favour of client ID metadata documents; both are served.
- **Discovery:** a 401 on `/mcp` carries `WWW-Authenticate: Bearer resource_metadata="<url>/.well-known/oauth-protected-resource/mcp"`. Protected resource metadata is served there and at the root well-known path, naming `<url>/mcp` and showntell itself as the one authorization server. Authorization server metadata at `/.well-known/oauth-authorization-server` advertises `S256` only, `client_id_metadata_document_supported`, `authorization_response_iss_parameter_supported`, and the `none` client auth method for public clients.
- **No scopes:** showntell has one permission, acting as the signed-in user, so it advertises no scopes and the challenge names none. A client may still register and ask for scopes; they are recorded and change nothing.
- **Resource:** an authorization or token request naming another `resource` is refused with `invalid_target`; scheme and host case and a trailing slash are forgiven. A request without one gets a token for `<url>/mcp`, the only resource there is. Every token records its resource and `/mcp` checks it.
- **Consent (a design addition):** the spec requires an OAuth proxy with a static client ID at the upstream provider, which showntell is, to get the user's consent for each client. After sign-in, `/oauth/consent` names the client, where its metadata lives (or that it registered itself), the host it returns to, and warns when that is the user's own computer. The buttons post JSON and the page navigates by script, because `form-action 'self'` would stop a form's redirect to the client.
- **Issuer in the response:** the redirect back carries `iss` (RFC 9207), on errors too.
- **Tokens:** `sta_` and `str_` prefixed random strings, SHA-256 hashed in `auth.db`. A chain starts at each code exchange; refreshing spends the old refresh token and the old access token. Reusing a spent refresh token or a used code revokes the whole chain. Code exchange and refresh both run the sign-in rule again on the claims kept from the user's last sign-in, under the current settings, and revoke the chain if it fails.
- **Client ID metadata documents:** fetched only from public addresses (every resolved address must be global), without redirects, at most 16 KB, kept for the response's `max-age` between one minute and a day (five minutes by default). The document's `client_id` must equal its URL; redirect URIs must be HTTPS or loopback HTTP; only public clients (`none`), so `private_key_jwt` is refused. The address is checked when resolved, not pinned for the connection, so a DNS rebinding race remains in principle; the container's outbound rules are the backstop.
- **Registration:** redirect URIs must be HTTPS or loopback HTTP. Registration is unauthenticated, so it is capped at 30 a minute server-wide. Registered clients are kept; clearing unused ones is left for later.

### Where the SDK fit and where it didn't

| Piece | Used | Why or why not |
|---|---|---|
| **Authorize, token, registration, revocation handlers** | Yes | `AuthServer` in `oauth.py` is their provider |
| **`OAuthClientInformationFull`, the token models** | Yes | |
| **`get_client` for metadata documents** | Yes | the provider fetches the document there when the client ID is an HTTPS URL; the handlers need nothing else |
| **Its metadata routes** | No | they leave out `client_id_metadata_document_supported`, `authorization_response_iss_parameter_supported` and the `none` auth method |
| **The token handler's `resource`** | Wrapped | it parses `resource` but never passes it on, so `ResourceCheck` refuses a wrong one before the handler runs |
| **Its bearer middleware and `AuthSettings`** | No | `HostedGuard` already resolves every request; it asks the login for the challenge and the token's user |

### Browser sign-in

- **Library:** Authlib's HTTPX client for the code exchange, joserfc (Authlib's JOSE library; `authlib.jose` is deprecated) for the ID token: signature from the provider's JWKS, refetched once for an unknown key, `iss`, `aud` (and `azp` when there are several audiences), `exp`, `iat` and `nonce`, with a minute's leeway. Discovery is one GET whose `issuer` must equal the configured one.
- **The flow:** `/login` keeps the state, nonce and PKCE verifier server-side and sets a ten-minute `showntell_login` cookie on `/oidc` holding the state; the callback must arrive in the same browser, which stops login CSRF. After sign-in the browser returns only to a path on this server.
- **Sessions:** in `auth.db` (hashed ids), so restarts keep viewers signed in. Signed-out page requests (`/` and `/w/{id}/`) are sent to `/login?next=...`; other routes get 401. `POST /logout` ends the session.
- **No signing key (a design change):** every piece of sign-in state is a random value looked up server-side, so nothing needs signing and the generated key the design mentioned is not made.

### The Share control

- **Where:** a "Share…" row in the player's settings menu, on the owner's view only: the page gets `share.js` and `share.css` only when the viewer owns it, so local pages and shared viewers' pages are unchanged. It opens a dialog listing who it is shared with (each with Remove), an address to add, and the company-wide switch.
- **Checks:** `GET` and `POST /w/{id}/shares`, owner only (404 for anyone else), POSTs behind the CSRF token. Both they and `share_walkthrough` call `LocalService.share_session`, so the shape and domain checks and the wake-up of open streams are the same.
- **Sign out:** with provider sign-in, the gallery's footer has a Sign out button (`POST /logout`).

### Left for later

- **Clearing unused registered clients**, and per-address limits on `/register` (behind a proxy every request comes from the proxy).
- **Pinning a metadata document's resolved address** for its fetch.

## Phase 3 as built

- **Files:** `Dockerfile` (Python 3.13 slim, uv, ffmpeg, Playwright's Chromium, the `video` and `hosted` extras), `deploy/compose.yaml` with Caddy, `deploy/Caddyfile`, `deploy/.env.example`, `deploy/chromium-seccomp.json`, and [self-hosting.md](self-hosting.md) with the Google and Entra registration steps. `showntell doctor` gains rows for the hosted settings, the provider's discovery document and Chromium's sandbox.
- **Seccomp (a design change):** the design asked for the sandbox on under Docker's default seccomp profile. Built and run (Docker 29.8, linux/arm64), that fails: the sandbox makes user namespaces and chroots inside them, and the default profile allows `clone` with namespace flags, `unshare`, `setns` and `chroot` only with `CAP_SYS_ADMIN` or `CAP_SYS_CHROOT`, which the container drops. Giving the container those capabilities would grant far more than the sandbox needs, and `--no-sandbox` would leave Chromium with none of its own, so the compose file uses Docker's default profile (moby/profiles at `6fe7deb`) plus an unconditional allow for exactly those four calls. Verified: with it, as uid 10001 with every capability dropped, `no-new-privileges` and a read-only root, Chromium starts sandboxed, the finish check draws the slides and an MP4 exports.
- **Sandbox in hosted mode:** Playwright passes `--no-sandbox` unless asked, so hosted mode now asks (`Hosted.chromium_sandbox`). The tests turn it off on Linux, where Ubuntu 24.04 runners forbid unprivileged user namespaces; macOS keeps it on.
- **Outbound traffic (a design gap):** the design limits it to the provider and the voice model download. Client ID metadata documents add a third kind of destination: the hosts of the clients' own metadata documents, which showntell fetches. The compose file restricts no outbound traffic; self-hosting.md lists what a firewall or egress proxy must allow.
- **Tool call logs:** `HostedGuard` reads the tool name and handle from each `/mcp` call and logs the user, tool, walkthrough and time once the response ends; the arguments never reach the log.
- **Not tried against real providers:** the sign-in and token tests run against `tests/fake_oidc.py`. The manual run per provider (sign in through Google and Entra, add the server to claude.ai and to Claude Code) is still to do.

## Open questions

- **Images:** with uploads out of v1, hosted walkthroughs can use agent-drawn SVG, diagrams and charts but not screenshots. An upload tool (size- and type-checked, re-encoded on the server) would bring them back.
- **Admins:** v1 has none. Someone may need to delete another user's walkthrough or see usage.
- **Retention:** 180 days is a guess; some companies will want a setting or no limit.
