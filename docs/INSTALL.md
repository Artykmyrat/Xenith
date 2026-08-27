# Installing Xenith on a server

Two ways to do it: the installer script, or the same steps by hand. Both end up
with the panel running under Docker, its data in `/var/lib/marzban`, and its
configuration in `/opt/xenith/.env`.

## Before you start

- A fresh **Debian 12** or **Ubuntu 22.04/24.04** server, root access.
- 1 vCPU / 1 GB RAM is enough for a few hundred users.
- A **domain** with an `A` record pointing at the server. Without one the panel
  is reachable only through an SSH tunnel and subscription links will not work.
- Ports **80** (certificate validation) and the panel port (**8000** by default)
  reachable from outside, plus whatever ports your inbounds use.

Check the DNS record before installing — certificate issuance fails otherwise:

```bash
dig +short panel.example.com
```

## Option 1 — the installer

```bash
curl -fsSL https://raw.githubusercontent.com/Artykmyrat/Xenith/main/scripts/install.sh -o install.sh
sudo bash install.sh --domain panel.example.com --email ops@example.com
```

It installs Docker, builds the image from this repository, obtains a Let's
Encrypt certificate over HTTP-01, generates `.env` with a random sudo password,
and starts the panel. At the end it prints the dashboard URL and the
credentials — save them, the password is only shown once.

Useful flags:

| Flag | Meaning |
|---|---|
| `--domain <host>` | Domain for HTTPS and subscription links |
| `--email <addr>` | Contact address for Let's Encrypt expiry notices |
| `--port <port>` | Panel port, default `8000` (use `443` to drop the port from URLs) |
| `--pull` | Use the published image instead of building from source |
| `--ref <branch\|tag>` | Build a specific ref |
| `--no-tls` | Skip certificate issuance |
| `--yes` | No confirmation prompt |

Re-running the script upgrades the panel: it rebuilds the image and restarts the
container, keeping `.env` and all data.

## Option 2 — by hand

**1. Docker**

```bash
apt-get update && apt-get install -y ca-certificates curl git openssl
curl -fsSL https://get.docker.com | sh
```

**2. Directories**

```bash
mkdir -p /opt/xenith /var/lib/marzban /etc/letsencrypt
```

**3. Image**

```bash
git clone --depth 1 https://github.com/Artykmyrat/Xenith.git /opt/xenith/src
docker build -t xenith:local /opt/xenith/src
```

**4. Certificate** (skip if a reverse proxy terminates TLS — see below)

certbot ships inside the image in its own virtualenv (`/opt/certbot`), so
nothing extra is installed on the host. Port 80 has to be free while this runs:

```bash
docker run --rm -p 80:80 -v /etc/letsencrypt:/etc/letsencrypt \
  --entrypoint certbot xenith:local \
  certonly --standalone --non-interactive --agree-tos \
  --email ops@example.com -d panel.example.com
```

**5. Configuration** — `/opt/xenith/.env`:

```ini
SUDO_USERNAME = "admin"
SUDO_PASSWORD = "<a long random password>"

UVICORN_HOST = "0.0.0.0"
UVICORN_PORT = 8000
UVICORN_SSL_CERTFILE = "/etc/letsencrypt/live/panel.example.com/fullchain.pem"
UVICORN_SSL_KEYFILE = "/etc/letsencrypt/live/panel.example.com/privkey.pem"

XRAY_SUBSCRIPTION_PATH = "<random string>"
XRAY_SUBSCRIPTION_URL_PREFIX = "https://panel.example.com:8000"

CERTBOT_ENABLED = True
CERTBOT_EMAIL = "ops@example.com"

LOGIN_RATE_LIMIT_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW = 300
```

```bash
chmod 600 /opt/xenith/.env
```

The panel refuses to bind to `0.0.0.0` without a certificate — that is
deliberate. See [.env.example](../.env.example) for every setting.

**6. Compose file** — `/opt/xenith/docker-compose.yml`:

```yaml
services:
  xenith:
    image: xenith:local
    restart: always
    env_file: .env
    network_mode: host
    volumes:
      - /var/lib/marzban:/var/lib/marzban
      - /etc/letsencrypt:/etc/letsencrypt
```

`/etc/letsencrypt` has to be a volume, otherwise certificates issued from the
Certificates screen disappear when the container is recreated.

**7. Start**

```bash
cd /opt/xenith && docker compose up -d
docker compose logs -f
```

Open `https://panel.example.com:8000/dashboard/` and sign in with
`SUDO_USERNAME` / `SUDO_PASSWORD`.

## Behind a reverse proxy

If nginx or Caddy terminates TLS instead, leave `UVICORN_SSL_*` unset (the panel
then listens on localhost only) and point the proxy at `127.0.0.1:8000`. Two
settings matter on the panel side:

```ini
TRUSTED_PROXIES = '127.0.0.1,::1'
```

Without it the panel ignores `X-Forwarded-For` and `X-Forwarded-Proto`, so every
login is logged as coming from the proxy and rate limiting counts all clients as
one. With it, only that address is believed — nobody else can forge the header.

Proxy `/dashboard/`, `/api/`, `/sub/` (or whatever `XRAY_SUBSCRIPTION_PATH` is)
and `/statics/`, and pass WebSocket upgrade headers so the live core log works.

## After installing

1. **Inbounds** — Settings → *Edit core config* holds the Xray configuration.
   Add your inbounds there and restart the core; they show up under Inbounds.
2. **Hosts** — Inbounds → *Edit hosts* controls what address ends up in the
   subscription links.
3. **Users** — Users → *New user*; the link and QR buttons on each row give the
   subscription.
4. **Certificates** — the Certificates screen issues and renews certificates
   through certbot. Standalone validation needs port 80 free for a moment;
   webroot validation needs a directory your web server already serves.

Certificates issued for the panel itself are picked up on restart:

```bash
docker compose -f /opt/xenith/docker-compose.yml restart
```

## Day to day

The installer puts a `xenith` command on the host that wraps the container:

```bash
xenith up                      # start
xenith down                    # stop
xenith restart                 # restart, e.g. after editing .env
xenith status                  # container state
xenith logs -f                 # follow the panel log
xenith update                  # pull the latest sources, rebuild, restart
xenith cli admin create --sudo # another sudo admin
xenith image <ref>             # switch to a published image, e.g. ghcr.io/artykmyrat/xenith:latest
xenith certbot certificates    # certbot, inside the container
xenith env                     # edit .env
xenith shell                   # shell inside the container
```

`xenith-cli` itself lives inside the container, so run it through `xenith cli`.
If you installed by hand, drop the wrapper in place yourself:

```bash
install -m 755 /opt/xenith/src/scripts/xenith /usr/local/bin/xenith
```

Everything it does is also available directly:

```bash
docker compose -f /opt/xenith/docker-compose.yml logs -f
docker compose -f /opt/xenith/docker-compose.yml exec xenith xenith-cli admin list
```

Backup — the database, the config and the certificates:

```bash
tar czf xenith-backup-$(date +%F).tar.gz /var/lib/marzban /opt/xenith/.env /etc/letsencrypt
```

Renewals: certbot's own timer does not exist inside the container, so renew
either from the Certificates screen or with a cron entry on the host:

```cron
0 3 * * * /usr/local/bin/xenith certbot renew --quiet && /usr/local/bin/xenith restart
```

## When something is wrong

| Symptom | Cause |
|---|---|
| Panel unreachable from outside | No certificate configured, so it binds to localhost. Issue one, or tunnel: `ssh -L 8000:localhost:8000 root@server` |
| `Address already in use` on port 80 during issuance | Another web server holds it. Stop it, or use webroot validation |
| Certificate issuance fails with NXDOMAIN | The domain does not resolve to this server yet |
| `429 Too Many Requests` on login | Brute-force protection. Wait out `LOGIN_RATE_LIMIT_WINDOW`, or raise the limit in `.env` |
| Every login shows the same IP | Set `TRUSTED_PROXIES` to your reverse proxy's address |
| Core state shows `Stopped` | Xray failed to start — check `docker compose logs` and the core config |

## Removing it

```bash
cd /opt/xenith && docker compose down
rm -rf /opt/xenith
# data and certificates, delete only if you mean it:
# rm -rf /var/lib/marzban /etc/letsencrypt
```

## Automatic updates

Point the host at the published image once:

```bash
xenith image ghcr.io/artykmyrat/xenith:latest
```

From then on every push to `main` in the repository rebuilds that image and
deploys it here, provided the SSH secrets are set — see [CI.md](./CI.md).
