<h1 align="center">SkyPanel</h1>

<p align="center">
    Unified GUI Censorship Resistant Solution Powered by <a href="https://github.com/XTLS/Xray-core">Xray</a>
</p>

<p align="center">
    <a href="./LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square" /></a>
</p>

---

## About

SkyPanel is a proxy management panel built on [Xray-core](https://github.com/XTLS/Xray-core).
It provides a web dashboard, a REST API, a CLI and a Telegram bot to manage users,
traffic limits and subscription links across one or many servers.

SkyPanel is a fork of [Marzban](https://github.com/Gozargah/Marzban) by Gozargah,
licensed under the GNU AGPL-3.0. It is an independent project and is **not**
affiliated with or endorsed by the Marzban maintainers. Please report issues here,
not to the upstream project.

## Migrating from Marzban

SkyPanel keeps full data compatibility with Marzban:

- the data directory stays at `/var/lib/marzban`
- database schema and Alembic revisions are unchanged
- existing subscription links keep working
- the `marzban-cli` command remains available as an alias of `skypanel-cli`
- `MARZBAN_ADMIN_PASSWORD` is still honoured alongside `SKYPANEL_ADMIN_PASSWORD`

In practice, migrating means pointing your `docker-compose.yml` at the SkyPanel
image and restarting. Back up `/var/lib/marzban` and your `.env` first.

## Quick start

```yaml
services:
  skypanel:
    image: artykmyrat/skypanel:latest
    restart: always
    env_file: .env
    network_mode: host
    volumes:
      - /var/lib/marzban:/var/lib/marzban
```

```bash
docker compose up -d
```

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

## Development

Requires Python 3.12 (the version the Docker image is built on) and Node.js.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd app/dashboard && npm install && cd ../..
```

Set `DEBUG=true` in `.env` and run `python main.py` — backend and frontend then
run separately with auto-reload.

Python code is formatted with `autopep8 <file> --max-line-length 120`.

## Nodes

Multi-server setups use [Marzban-Node](https://github.com/Gozargah/Marzban-node)
on the remote machines. SkyPanel speaks the same protocol, so upstream nodes work
unchanged.

Nodes serve a self-signed certificate, which SkyPanel pins on the first
successful connection and requires on every connection after that — an
intercepted link is refused rather than silently trusted. If a node is
reinstalled it generates a new certificate, and the panel will refuse it until
the pin is cleared:

```
POST /api/node/{node_id}/reset-certificate
```

Only clear a pin when you expect the certificate to have changed.

## Documentation

SkyPanel's own documentation is still being written. Until then, the upstream
Marzban documentation applies to most features and is mirrored under
[docs/upstream/](./docs/upstream/). Note that installation instructions there
refer to Gozargah's install scripts, which SkyPanel does not use.

## License

[GNU Affero General Public License v3.0](./LICENSE).

Because SkyPanel is AGPL-licensed and runs as a network service, anyone who uses
a modified instance is entitled to receive its source code. Forks and derived
works must stay under the same license.

Original work Copyright © Gozargah (Marzban).
Modifications Copyright © SkyPanel contributors.
