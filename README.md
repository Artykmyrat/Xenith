<h1 align="center">Xenith</h1>

<p align="center">
    Unified GUI Censorship Resistant Solution Powered by <a href="https://github.com/XTLS/Xray-core">Xray</a>
</p>

<p align="center">
    <a href="./LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square" /></a>
</p>

---

## About

Xenith is a proxy management panel built on [Xray-core](https://github.com/XTLS/Xray-core).
It provides a web dashboard, a REST API, a CLI and a Telegram bot to manage users,
traffic limits and subscription links across one or many servers.

Xenith is a fork of [Marzban](https://github.com/Gozargah/Marzban) by Gozargah,
licensed under the GNU AGPL-3.0. It is an independent project and is **not**
affiliated with or endorsed by the Marzban maintainers. Please report issues here,
not to the upstream project.

## Migrating from Marzban

Xenith keeps full data compatibility with Marzban:

- the data directory stays at `/var/lib/marzban`
- database schema and Alembic revisions are unchanged
- existing subscription links keep working
- the `marzban-cli` and `skypanel-cli` commands remain available as aliases of `xenith-cli`
- `MARZBAN_ADMIN_PASSWORD` and `SKYPANEL_ADMIN_PASSWORD` are still honoured alongside `XENITH_ADMIN_PASSWORD`

In practice, migrating means pointing your `docker-compose.yml` at the Xenith
image and restarting. Back up `/var/lib/marzban` and your `.env` first.

## Upgrading from SkyPanel

The panel was called SkyPanel until this release. Nothing in your data changes:

- point `docker-compose.yml` at `artykmyrat/xenith` and restart
- `skypanel-cli` still works, as an alias of `xenith-cli`
- `SKYPANEL_ADMIN_PASSWORD` is still read
- dashboard sessions are signed under a new cookie name, so everyone signs in once more

## Quick start

```yaml
services:
  xenith:
    image: artykmyrat/xenith:latest
    restart: always
    env_file: .env
    network_mode: host
    volumes:
      - /var/lib/marzban:/var/lib/marzban
```

```bash
docker compose up -d
```

For a fresh server there is an installer that does all of this — Docker, image,
certificate, `.env` and first start — in one go:

```bash
curl -fsSL https://raw.githubusercontent.com/Artykmyrat/Xenith/main/scripts/install.sh -o install.sh
sudo bash install.sh --domain panel.example.com --email ops@example.com
```

It also installs a `xenith` command on the host:

```bash
xenith logs -f                 # panel logs
xenith restart                 # restart after editing .env
xenith cli admin create --sudo # add an admin
xenith update                  # pull or rebuild the image and restart
```

Pushing to `main` builds the image and can deploy it to the server on its own —
see [docs/CI.md](./docs/CI.md).

See [docs/INSTALL.md](./docs/INSTALL.md) for the manual steps, running behind a
reverse proxy, upgrades and backups.

Copy `.env.example` to `.env` and set at least `SUDO_USERNAME` and `SUDO_PASSWORD`
before the first run. The dashboard is served at `/dashboard/`.

For security reasons the panel binds to localhost unless `UVICORN_SSL_CERTFILE`
and `UVICORN_SSL_KEYFILE` are configured. Put it behind a reverse proxy with TLS,
or use SSH port forwarding:

```bash
ssh -L 8000:localhost:8000 user@serverip
```

## Configuration

All settings are read from environment variables or the `.env` file.
See [.env.example](./.env.example) for the full list with comments.

### Running behind a reverse proxy

Forwarding headers are only believed when the request comes from a proxy you
list in `TRUSTED_PROXIES` (IPs or CIDRs, `*` to trust every peer). By default
the list is empty and `X-Forwarded-For` / `X-Real-IP` are ignored, so a client
talking to the panel directly cannot forge the address used for login
notifications and login rate limiting.

If nginx or Caddy sits in front of the panel, set it to the proxy's address —
otherwise every login will look like it came from the proxy itself:

```ini
TRUSTED_PROXIES = '127.0.0.1,::1'
```

Failed logins are rate limited per client address: `LOGIN_RATE_LIMIT_ATTEMPTS`
failures within `LOGIN_RATE_LIMIT_WINDOW` seconds return `429` until the window
slides past. A successful login clears the counter.

### Sessions

The dashboard authenticates with an httpOnly `SameSite=Strict` cookie set by
`POST /api/admin/token`, so no script on the page can read the JWT and no
cross-site request can carry it. `Secure` is added automatically when the
request arrives over HTTPS, including through a proxy listed in
`TRUSTED_PROXIES` that sets `X-Forwarded-Proto`.

The same response still returns `access_token` in its body, so the CLI and any
API client keep working unchanged with the `Authorization: Bearer` header.
`POST /api/admin/logout` clears the cookie.

Cross-origin browser access is off unless `ALLOWED_ORIGINS` lists the origins
that need it; the bundled dashboard is same-origin, so the default suits most
installs. A `*` there disables credentialed requests (browsers reject a wildcard
combined with credentials) and logs a warning at startup — list exact origins
instead. With `DEBUG=true` the Vite dev server origins are allowed automatically.

## Development

Requires Python 3.12 (the version the Docker image is built on) and Node.js.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd app/dashboard && npm install && cd ../..
```

Set `DEBUG=true` in `.env` and run `python main.py` — backend and frontend then
run separately with auto-reload. Because the session cookie is `SameSite=Strict`,
open the dev server on the same host the API uses (`http://127.0.0.1:3000` when
`VITE_BASE_API` points at `127.0.0.1`); `DEBUG` allows both dev server origins
through CORS on its own.

Python code is formatted with `autopep8 <file> --max-line-length 120`.

## Nodes

Multi-server setups use [Marzban-Node](https://github.com/Gozargah/Marzban-node)
on the remote machines. Xenith speaks the same protocol, so upstream nodes work
unchanged.

Nodes serve a self-signed certificate, which Xenith pins on the first
successful connection and requires on every connection after that — an
intercepted link is refused rather than silently trusted. If a node is
reinstalled it generates a new certificate, and the panel will refuse it until
the pin is cleared:

```
POST /api/node/{node_id}/reset-certificate
```

Only clear a pin when you expect the certificate to have changed.

## Documentation

Xenith's own documentation is still being written. Until then, the upstream
Marzban documentation applies to most features and is mirrored under
[docs/upstream/](./docs/upstream/). Note that installation instructions there
refer to Gozargah's install scripts, which Xenith does not use.

## License

[GNU Affero General Public License v3.0](./LICENSE).

Because Xenith is AGPL-licensed and runs as a network service, anyone who uses
a modified instance is entitled to receive its source code. Forks and derived
works must stay under the same license.

Original work Copyright © Gozargah (Marzban).
Modifications Copyright © Xenith contributors.
