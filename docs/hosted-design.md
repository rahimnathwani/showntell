# Hosted mode: design

Status: proposed, 2026-10-28. Not built.

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

The server refuses to start in hosted mode with any of these missing or with a `http://` public URL other than localhost.

## Sign-in

showntell is both the MCP resource server and its own OAuth authorization server. It delegates the login itself to the company's provider and issues its own tokens. Google and Entra tokens never reach `/mcp`: accepting them would let any app holding one call showntell (the spec forbids this token passthrough).

```
MCP client ──/mcp, no token──▶ 401 + WWW-Authenticate: resource_metadata
           ──discovery──────▶ /.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server
           ──register───────▶ client ID metadata document, or dynamic registration
           ──/authorize (PKCE S256)──▶ showntell ──OIDC──▶ Google / Entra ──▶ /oidc/callback
                                        checks identity and domain, records the user
           ◀──code──  ──/token──▶ showntell access + refresh tokens (audience = <url>/mcp)
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
- **Where shares are made:** a new MCP tool `share_walkthrough(walkthrough, add=[...], remove=[...], company=true|false)`, and a Share control on the owner's view of the page. Its tool description is agent-facing text: a fresh agent writes it (see the clean-room rule).
- **404, not 403:** nothing tells a non-owner that an ID exists. IDs stay 16 random bytes.
- **Exports:** MP4 and folder exports go to the owner only. An exported folder plays offline without sign-in, so sharing it is the owner's act, outside the server's control.
- **Routes that go:** `/api/rpc/*` (the stdio shim's route) and embedding with `Access-Control-Allow-Origin: *` are off in hosted mode.

## Slide content is untrusted

An agent can be steered by what it reads, so its Markdown is treated as hostile.

| Risk | Today | Hosted mode |
|---|---|---|
| **Script in a slide** | SVG rebuilt from an allowlist; draw.io parsing skips scripts; the player escapes text | The same, plus a CSP on every page: `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; img-src 'self' data:`. Remove any inline script the player needs; check whether mermaid needs `unsafe-eval` and confine it if so |
| **Reading the server's disk** | Code, diffs, images and git are read under `project_dir` | `project_dir`, file and git sources and local image paths are refused with a reply that says to put code and diffs in the Markdown. The reply and guide wording come from a fresh agent |
| **Server-side requests (SSRF)** | Remote image URLs are allowed; the checks and export Chromium loads them | Remote images refused when slides are added (`img-src` blocks them anyway). The checks and export Chromium routes every request through an allowlist of showntell's own origin and aborts the rest. The container's outbound traffic is limited to the OIDC provider and the voice model download |
| **Hostile Markdown at parse time** | — | Size caps on a slide and a walkthrough before parsing; parser timeouts |

Later, optionally: serve walkthrough content from a second origin (`SHOWNTELL_CONTENT_URL`) so that a hole in the player can't use the main origin's session. It needs a sign-in handoff between origins, so it is left out of v1.

## Fair use

| Limit | Default |
|---|---|
| **Narration per walkthrough** | 8 minutes; `too_long` becomes an error past it |
| **Slides per walkthrough** | 60 |
| **Walkthroughs in progress per user** | 3 |
| **Stored per user** | 200 walkthroughs or 2 GB |
| **Retention** | 180 days after last view, then deleted |
| **Speech** | One queue, served round-robin across users, so one long walkthrough doesn't hold up others |
| **Chromium** | 2 pages at once server-wide, 60 s per check, 15 min per export |
| **Requests** | 60 tool calls a minute per user |

## Process

- **One process for everyone:** user input is never run as code; Chromium has its own sandbox. Per-user processes would load the voice model and Chromium once each.
- **Container:** non-root user, read-only root filesystem, the data folder as the only writable volume, Chromium sandbox on (no `--no-sandbox`), default seccomp profile.
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

## Open questions

- **Images:** with uploads out of v1, hosted walkthroughs can use agent-drawn SVG, diagrams and charts but not screenshots. An upload tool (size- and type-checked, re-encoded on the server) would bring them back.
- **Admins:** v1 has none. Someone may need to delete another user's walkthrough or see usage.
- **Retention:** 180 days is a guess; some companies will want a setting or no limit.
