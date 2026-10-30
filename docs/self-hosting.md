# Self-hosting showntell for a company

One showntell server for everyone at a company: people add it to Claude by URL, sign in with the company's Google or Microsoft Entra account, and each sees their own walkthroughs and those shared with them. The design, and why it works this way, is in [hosted-design.md](hosted-design.md).

> **Tried with Entra and Claude Code only.** On 2026-10-29 a server on localhost signed in through a real Entra tenant, in the browser and from Claude Code's MCP sign-in, and made walkthroughs over `/mcp`; that trial found a bug fixed in 0.2.1 (Claude Code's sign-in port changes per attempt). Google sign-in and claude.ai connectors (which need a public HTTPS name) have passed only the tests against a fake OIDC provider. Do a trial run before people rely on those, and report what breaks.

## What you need

| | |
|---|---|
| **A machine** | Linux with Docker and Compose; 4 CPUs and 8 GB of memory serve a few people at once. Speech runs on the CPU (Kokoro on ONNX) |
| **A name** | A DNS name pointing at the machine, e.g. `showntell.example.com`, with ports 80 and 443 open to Caddy, which fetches the certificate |
| **Reachability** | For claude.ai connectors the server must be reachable from the internet: Claude's cloud calls it. Claude Code and Claude Desktop connect from users' machines, so an intranet-only name works for them |
| **A sign-in app** | An OAuth client at Google, an app registration in Entra, or a client at another OIDC provider (below) |

## 1. Register showntell with the sign-in provider

The redirect URI is the same for every provider: `https://<your name>/oidc/callback`. showntell asks for `openid email profile` and nothing else.

### Google Workspace

1. In the Google Cloud console, pick or create a project in your Workspace organisation.
2. **Google Auth Platform → Audience:** set the user type to **Internal**, so only accounts in your organisation can sign in at all. (Older consoles call this the OAuth consent screen.)
3. **Branding:** an app name such as "showntell" and a support email.
4. **Clients → Create client:** type **Web application**; under **Authorised redirect URIs** add `https://<your name>/oidc/callback`.
5. Copy the client ID and client secret.

Settings: `SHOWNTELL_OIDC=google`, the client ID and secret, and `SHOWNTELL_ALLOWED_DOMAINS` set to your Workspace domain (several, comma-separated, if you have more). showntell accepts an account only when Google's `hd` claim is one of those domains, so personal @gmail.com accounts never get in, whatever the consent screen says.

### Microsoft Entra ID

1. In the Microsoft Entra admin center: **Identity → Applications → App registrations → New registration**.
2. **Supported account types:** **Accounts in this organizational directory only (single tenant)**.
3. **Redirect URI:** platform **Web**, `https://<your name>/oidc/callback`.
4. On the app's **Overview**, copy the **Application (client) ID** and the **Directory (tenant) ID**, a GUID.
5. **Certificates & secrets → New client secret:** copy its value now; it is shown once. Note when it expires and put a reminder in the calendar: sign-in stops when it does.
6. **API permissions:** the default delegated Microsoft Graph `User.Read` is enough; `openid`, `email` and `profile` are granted with it.

Settings: `SHOWNTELL_OIDC=entra`, the client ID and secret, `SHOWNTELL_ENTRA_TENANT` set to the tenant ID (not a domain). Leave `SHOWNTELL_ALLOWED_DOMAINS` empty to admit anyone in the tenant, or list domains to admit only users whose user principal name (UPN) is in them. showntell reads the UPN (`preferred_username`), never Entra's `email` claim, which users and admins can edit.

### Another OIDC provider

Any provider with discovery at `<issuer>/.well-known/openid-configuration`, PKCE with S256, RS256, ES256 or PS256 ID tokens, and an `email_verified` claim. Settings: `SHOWNTELL_OIDC=generic`, `SHOWNTELL_OIDC_ISSUER`, the client ID and secret, and `SHOWNTELL_ALLOWED_DOMAINS`: an account gets in only when `email_verified` is true and its email's domain is listed.

## 2. Configure and start

```sh
git clone https://github.com/rahimnathwani/showntell && cd showntell
cp deploy/.env.example deploy/.env      # holds the client secret: never commit it
$EDITOR deploy/.env                     # SHOWNTELL_DOMAIN, the sign-in settings
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml exec showntell showntell doctor
```

`doctor` checks the settings, fetches the provider's discovery document, starts Chromium with its sandbox, and loads the voice. On first start the server downloads the Kokoro voice model (about 330 MB, from Hugging Face) and the spaCy English model (from GitHub) into its data volume; later starts use the copies.

The server refuses to start, with a message naming the setting, when one is missing or unsafe: no HTTPS public URL, Google or generic sign-in without allowed domains, an Entra tenant that is not a GUID, or the dev login beside real sign-in.

## 3. Add it to Claude

The MCP endpoint is `https://<your name>/mcp`. Each person signs in the first time their client connects: a browser window opens, they sign in with the company account, and showntell asks them to approve the client by name.

| Client | How |
|---|---|
| **claude.ai** | Settings → Connectors → Add custom connector, with the endpoint. On Team and Enterprise plans an owner can add it for the organisation |
| **Claude Code** | `claude mcp add --transport http --scope user showntell https://<your name>/mcp`, then `/mcp` to sign in |
| **Claude Desktop** | Settings → Connectors, as for claude.ai |

The gallery at `https://<your name>/` lists each person's walkthroughs and those shared with them. Owners share from a walkthrough's settings menu (Share…), or by asking Claude to share it.

## What the container does to stay contained

| | |
|---|---|
| **User** | uid 10001, not root |
| **Filesystem** | read-only root; the `showntell-data` volume (`/data`) and a 512 MB `/tmp` are the only writable places |
| **Capabilities** | all dropped, `no-new-privileges` |
| **Chromium** | runs with its own sandbox. That sandbox makes user namespaces and chroots inside them, which Docker's default seccomp profile allows only with capabilities the container drops, so `deploy/chromium-seccomp.json` is Docker's default profile (from moby/profiles at commit `6fe7deb`, rebuilt by `scripts/seccomp_profile.py`) plus `chroot`, `clone`, `setns` and `unshare`. Without it `doctor` reports `chromium sandbox` as failing and the layout check and exports stop |
| **TLS** | Caddy, with automatic certificates |
| **Logs** | sign-ins, approved clients, and each tool call: user, tool, walkthrough and seconds. Never narration, code or tokens |

**Chromium on Ubuntu 23.10 and later:** the host also forbids unprivileged user namespaces to processes whose AppArmor profile does not grant them, and Docker's default AppArmor profile does not. There the sandbox fails until you lift the restriction on the host, `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (and a line in `/etc/sysctl.d/` to keep it), or give the container an AppArmor profile that allows `userns`. `showntell doctor` in the container shows which: the `chromium sandbox` row. CI does the first before its container check.

**Checking a build:** `scripts/container_check.sh` builds the image, runs it locked down as the compose file does with the dev login and fake speech, and drives one walkthrough through MCP to a finish check and an MP4 export. CI runs it on pull requests that touch the image, `deploy/` or hosted-mode code.

**Outbound traffic** is not restricted by the compose file. Restrict it with the host's firewall or an egress proxy to:

| Destination | Why |
|---|---|
| The sign-in provider | `accounts.google.com`, `oauth2.googleapis.com` and `www.googleapis.com` (keys), or `login.microsoftonline.com` |
| `huggingface.co` and its CDN, `github.com` and `objects.githubusercontent.com` | the voice and language models, on first start only |
| The hosts of MCP clients' metadata documents | clients that identify themselves by a client ID metadata document (an HTTPS URL) have it fetched once in a while; showntell refuses private addresses itself. Clients that register dynamically need none |

## Data and backups

Everything lives in the `showntell-data` volume: walkthroughs and their exports, the shared audio cache, the models, and `auth.db` (users, registered clients, sessions and tokens, every secret stored only as a hash). Back up the volume; losing `auth.db` signs everyone out and makes clients register again, nothing more.

Claude stays connected for `SHOWNTELL_SIGNIN_DAYS` (7) after a person last signed in at the provider, then asks them to sign in again; that is how someone disabled at Google or Entra loses access without anyone changing showntell. Walkthroughs nobody has opened or changed for 180 days are deleted (`SHOWNTELL_QUOTA_RETENTION_DAYS`; 0 keeps them). The other limits are in `deploy/.env.example` and [hosted-design.md](hosted-design.md#fair-use).

## Updating

```sh
git pull && docker compose -f deploy/compose.yaml up -d --build
```

## Trying it on one machine first

Without a provider, `SHOWNTELL_DEV_USERS` gives a stand-in login for a few named addresses, for a trial on your own computer only: it works only with `SHOWNTELL_HOST` on loopback and an `http://localhost` public URL, refuses any request from another machine or through a proxy (one carrying `X-Forwarded-For`, `Forwarded` or `X-Real-IP`), and the log prints each user's bearer token and sign-in link. Never use it in production.

```sh
SHOWNTELL_MODE=hosted SHOWNTELL_PUBLIC_URL=http://localhost:4747 SHOWNTELL_DEV_USERS=you@example.com \
  uvx --from 'showntell[video,hosted] @ git+https://github.com/rahimnathwani/showntell' showntell serve
```
