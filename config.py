from decouple import config
from dotenv import load_dotenv

load_dotenv()


SQLALCHEMY_DATABASE_URL = config("SQLALCHEMY_DATABASE_URL", default="sqlite:///db.sqlite3")
SQLALCHEMY_POOL_SIZE = config("SQLALCHEMY_POOL_SIZE", cast=int, default=10)
SQLIALCHEMY_MAX_OVERFLOW = config("SQLIALCHEMY_MAX_OVERFLOW", cast=int, default=30)

UVICORN_HOST = config("UVICORN_HOST", default="0.0.0.0")
UVICORN_PORT = config("UVICORN_PORT", cast=int, default=8000)
UVICORN_UDS = config("UVICORN_UDS", default=None)
UVICORN_SSL_CERTFILE = config("UVICORN_SSL_CERTFILE", default=None)
UVICORN_SSL_KEYFILE = config("UVICORN_SSL_KEYFILE", default=None)
UVICORN_SSL_CA_TYPE = config("UVICORN_SSL_CA_TYPE", default="public").lower()
DASHBOARD_PATH = config("DASHBOARD_PATH", default="/dashboard/")

DEBUG = config("DEBUG", default=False, cast=bool)
DOCS = config("DOCS", default=False, cast=bool)

# Browser origins allowed to call the API cross-origin. Empty by default: the
# bundled dashboard is served from the same origin and needs no CORS at all.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in config("ALLOWED_ORIGINS", default="").split(",")
    if origin.strip()
]

VITE_BASE_API = (
    f"{'https' if UVICORN_SSL_CERTFILE and UVICORN_SSL_KEYFILE else 'http'}://127.0.0.1:{UVICORN_PORT}/api/"
    if DEBUG and config("VITE_BASE_API", default="/api/") == "/api/"
    else config("VITE_BASE_API", default="/api/")
)

XRAY_JSON = config("XRAY_JSON", default="./xray_config.json")
XRAY_FALLBACKS_INBOUND_TAG = config("XRAY_FALLBACKS_INBOUND_TAG", cast=str, default="") or config(
    "XRAY_FALLBACK_INBOUND_TAG", cast=str, default=""
)
XRAY_EXECUTABLE_PATH = config("XRAY_EXECUTABLE_PATH", default="/usr/local/bin/xray")
XRAY_ASSETS_PATH = config("XRAY_ASSETS_PATH", default="/usr/local/share/xray")
XRAY_EXCLUDE_INBOUND_TAGS = config("XRAY_EXCLUDE_INBOUND_TAGS", default='').split()
# The host a generated REALITY inbound borrows its handshake from. Left empty,
# the panel measures a handful of candidates and keeps the one this server
# reaches fastest, which is what a client feels as connect time. Set it to
# pin one — a site on this machine's own 443, say, which costs no round trip
# at all.
XRAY_REALITY_DEST = config("XRAY_REALITY_DEST", default="").strip()
# What the core's Go runtime is told when the panel starts it. GOGC is the
# percentage the heap may grow between collections: 100 is the Go default and
# is tuned for programs that hold their data, while a proxy allocates per
# connection and frees almost all of it, so collecting that often costs CPU
# and adds a pause a connection waits through. The memory share becomes
# GOMEMLIMIT, which is what stops a larger GOGC from running the machine out
# of memory. Either can be set to 0 to leave the runtime alone.
# The flow a VLESS account is created with when nothing asks for another.
# Vision keeps the core from encrypting a stream that is already encrypted --
# TLS inside TLS, which is most of what a VLESS connection costs in CPU. It
# only applies where it can: the core and the subscription both drop it from
# an inbound that is not raw TCP over TLS or REALITY, so it is safe to hand
# every account. Set it empty for the old behaviour of no flow at all.
XRAY_DEFAULT_VLESS_FLOW = config("XRAY_DEFAULT_VLESS_FLOW", default="xtls-rprx-vision")
XRAY_GOGC = config("XRAY_GOGC", cast=int, default=200)
XRAY_MEMORY_LIMIT_PERCENT = config("XRAY_MEMORY_LIMIT_PERCENT", cast=int, default=60)
XRAY_SUBSCRIPTION_URL_PREFIX = config("XRAY_SUBSCRIPTION_URL_PREFIX", default="").strip("/")
XRAY_SUBSCRIPTION_PATH = config("XRAY_SUBSCRIPTION_PATH", default="sub").strip("/")

TELEGRAM_API_TOKEN = config("TELEGRAM_API_TOKEN", default="")
TELEGRAM_ADMIN_ID = config(
    'TELEGRAM_ADMIN_ID',
    default="",
    cast=lambda v: [int(i) for i in filter(str.isdigit, (s.strip() for s in v.split(',')))]
)
TELEGRAM_PROXY_URL = config("TELEGRAM_PROXY_URL", default="")
TELEGRAM_LOGGER_CHANNEL_ID = config("TELEGRAM_LOGGER_CHANNEL_ID", cast=int, default=0)
TELEGRAM_LOGGER_TOPIC_ID = config("TELEGRAM_LOGGER_TOPIC_ID", cast=int, default=0)
TELEGRAM_DEFAULT_VLESS_FLOW = config("TELEGRAM_DEFAULT_VLESS_FLOW", default="")

JWT_ACCESS_TOKEN_EXPIRE_MINUTES = config("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", cast=int, default=1440)

# Brute-force protection for the admin login endpoint. A client IP that fails
# LOGIN_RATE_LIMIT_ATTEMPTS times within LOGIN_RATE_LIMIT_WINDOW seconds gets
# 429 until the window slides past. Set attempts to 0 to disable.
LOGIN_RATE_LIMIT_ATTEMPTS = config("LOGIN_RATE_LIMIT_ATTEMPTS", cast=int, default=5)
LOGIN_RATE_LIMIT_WINDOW = config("LOGIN_RATE_LIMIT_WINDOW", cast=int, default=300)

# TLS certificates issued through certbot from the Certificates screen. Off by
# default: it lets the panel run a system binary, so it has to be turned on
# deliberately on a host where certbot is installed and the panel runs as root.
CERTBOT_ENABLED = config("CERTBOT_ENABLED", default=False, cast=bool)
# Resolved through PATH by default, which finds both the distro package
# (/usr/bin) and the one installed from requirements.txt (/usr/local/bin).
CERTBOT_EXECUTABLE_PATH = config("CERTBOT_EXECUTABLE_PATH", default="certbot")
# Registration email for Let's Encrypt expiry notices; may be set per request.
CERTBOT_EMAIL = config("CERTBOT_EMAIL", default="")
# Default directory for webroot validation, when the panel sits behind a server
# that already serves .well-known/acme-challenge.
CERTBOT_WEBROOT = config("CERTBOT_WEBROOT", default="")
# Use the Let's Encrypt staging environment; certificates are untrusted but the
# rate limits are far looser, which is what you want while setting this up.
CERTBOT_STAGING = config("CERTBOT_STAGING", default=False, cast=bool)
CERTBOT_TIMEOUT = config("CERTBOT_TIMEOUT", cast=int, default=180)

# Kernel tuning from the System settings screen. Off by default: writing
# /proc/sys needs root, and a container only has it when started privileged,
# so this has to be turned on deliberately on a host that can carry it.
SYSCTL_ENABLED = config("SYSCTL_ENABLED", default=False, cast=bool)
SYSCTL_EXECUTABLE_PATH = config("SYSCTL_EXECUTABLE_PATH", default="sysctl")
# The one file the panel owns. Everything it applies is written here, and
# nothing else under /etc/sysctl.d is read or touched.
SYSCTL_CONF_PATH = config("SYSCTL_CONF_PATH", default="/etc/sysctl.d/99-xenith.conf")
# Where the live values are read from; only ever changed by the tests.
SYSCTL_PROC_PATH = config("SYSCTL_PROC_PATH", default="/proc/sys")
SYSCTL_TIMEOUT = config("SYSCTL_TIMEOUT", cast=int, default=30)

# Hysteria2, supervised beside the xray core rather than served by it: it is a
# daemon of its own, with its own configuration, authentication and statistics.
# Off by default, and main-server only — nodes carry xray alone.
HYSTERIA_ENABLED = config("HYSTERIA_ENABLED", default=False, cast=bool)
HYSTERIA_EXECUTABLE_PATH = config("HYSTERIA_EXECUTABLE_PATH", default="/usr/local/bin/hysteria")
# The rendered configuration. Written on every start, so editing it by hand
# lasts until the next one.
HYSTERIA_CONFIG_PATH = config("HYSTERIA_CONFIG_PATH", default="/var/lib/marzban/hysteria.yaml")
HYSTERIA_PORT = config("HYSTERIA_PORT", cast=int, default=443)
# Which certbot certificate to serve. Empty takes the first one the panel holds,
# which is what a single-domain install has.
HYSTERIA_DOMAIN = config("HYSTERIA_DOMAIN", default="")
# Salamander obfuscation. Empty leaves it off; a password turns it on, and every
# client then needs the same one.
HYSTERIA_OBFS_PASSWORD = config("HYSTERIA_OBFS_PASSWORD", default="")
# Bandwidth hints, in Mbps. Zero omits them, which leaves hysteria on BBR
# rather than its own congestion control.
HYSTERIA_UP_MBPS = config("HYSTERIA_UP_MBPS", cast=int, default=0)
HYSTERIA_DOWN_MBPS = config("HYSTERIA_DOWN_MBPS", cast=int, default=0)
# What an unauthenticated visitor to the port is shown instead of an error.
HYSTERIA_MASQUERADE_URL = config("HYSTERIA_MASQUERADE_URL", default="https://www.microsoft.com/")
# The daemon's traffic API, which the panel polls for usage. Loopback only: the
# secret is the whole of its authentication.
HYSTERIA_STATS_PORT = config("HYSTERIA_STATS_PORT", cast=int, default=25413)
# QUIC flow-control windows, in megabytes. A receiver may only be this far
# ahead of what it has acknowledged, so on a link with a long round trip the
# window -- not the bandwidth -- is what caps the speed: a 20 MB connection
# window over 200 ms cannot exceed about 800 Mbps however wide the pipe is.
# Both are twice what the daemon uses on its own. The cost is memory, and only
# for data actually in flight, so it is paid by fast clients rather than by
# every connected one. Set either to 0 to leave the daemon's own default.
HYSTERIA_QUIC_STREAM_WINDOW_MB = config("HYSTERIA_QUIC_STREAM_WINDOW_MB", cast=int, default=16)
HYSTERIA_QUIC_CONN_WINDOW_MB = config("HYSTERIA_QUIC_CONN_WINDOW_MB", cast=int, default=40)

# nginx, managed from the Nginx screen. Off by default: it edits files under
# /etc/nginx and signals a process outside the container, which only works when
# the compose file mounts the config directory and shares the host's PID
# namespace. See docs/INSTALL.md.
NGINX_ENABLED = config("NGINX_ENABLED", default=False, cast=bool)
NGINX_EXECUTABLE_PATH = config("NGINX_EXECUTABLE_PATH", default="nginx")
NGINX_CONF_DIR = config("NGINX_CONF_DIR", default="/etc/nginx")
NGINX_SITES_AVAILABLE = config("NGINX_SITES_AVAILABLE", default="/etc/nginx/sites-available")
NGINX_SITES_ENABLED = config("NGINX_SITES_ENABLED", default="/etc/nginx/sites-enabled")
# Where uploaded pages land. Everything written through the panel stays inside
# this directory; a path that resolves outside it is refused.
NGINX_WEBROOT = config("NGINX_WEBROOT", default="/var/www/html")
NGINX_LOG_DIR = config("NGINX_LOG_DIR", default="/var/log/nginx")
NGINX_TIMEOUT = config("NGINX_TIMEOUT", cast=int, default=30)
# Cap on a single uploaded file. The panel serves static pages, not archives.
NGINX_MAX_UPLOAD_BYTES = config("NGINX_MAX_UPLOAD_BYTES", cast=int, default=5 * 1024 * 1024)

# Resource limits. The panel always raises its own soft limits to the hard
# ceiling at startup, which needs no privilege at all. Writing the host's limit
# files is separate and off by default, because those live under /etc and only
# take effect once something is restarted.
ULIMIT_ENABLED = config("ULIMIT_ENABLED", default=False, cast=bool)
# What "maximum" means for open files. 1048576 is the kernel's own fs.nr_open
# default; going far past it breaks software that walks its descriptor table.
ULIMIT_TARGET_NOFILE = config("ULIMIT_TARGET_NOFILE", cast=int, default=1048576)
# Login sessions on the host, through PAM. Does not reach systemd services or
# containers, which is why the two paths below exist as well.
ULIMIT_LIMITS_CONF_PATH = config(
    "ULIMIT_LIMITS_CONF_PATH", default="/etc/security/limits.d/99-xenith.conf"
)
# The default every systemd unit on the host inherits.
ULIMIT_SYSTEMD_CONF_PATH = config(
    "ULIMIT_SYSTEMD_CONF_PATH", default="/etc/systemd/system.conf.d/99-xenith.conf"
)
# The default every container gets from the Docker daemon.
ULIMIT_DOCKER_DAEMON_PATH = config("ULIMIT_DOCKER_DAEMON_PATH", default="/etc/docker/daemon.json")

# Backups, made and restored from the Backup screen. A backup is one gzipped
# tar holding the database, the .env the panel was started with, the xray
# configuration and the small files under the data directory — the same four
# things a Marzban backup carries, which is what makes one importable here.
# On unless the directory cannot be created, because a panel that cannot be
# restored is the one failure with nothing behind it.
BACKUP_ENABLED = config("BACKUP_ENABLED", default=True, cast=bool)
# Where archives are kept. Inside the data volume, so they survive the
# container they were made in.
BACKUP_DIR = config("BACKUP_DIR", default="/var/lib/marzban/backups")
# The directory whose small files ride along in a backup: certificates, the
# hysteria configuration, custom templates. The backup directory itself and
# any database data directory under it are always left out.
BACKUP_DATA_DIR = config("BACKUP_DATA_DIR", default="/var/lib/marzban")
# The environment file to archive and, when asked, to restore. On a Docker
# install this is on the host rather than in the container, so it is only in a
# backup when the install directory is mounted — see docs/INSTALL.md. Missing,
# it is reported as skipped rather than failing the backup.
BACKUP_ENV_FILE = config("BACKUP_ENV_FILE", default="/opt/xenith/.env")
# A single file this much larger than a configuration file is data, not
# configuration — geoip databases and core binaries live under the data
# directory too. Skipped files are listed in the archive's manifest.
BACKUP_MAX_FILE_BYTES = config("BACKUP_MAX_FILE_BYTES", cast=int, default=32 * 1024 * 1024)
# Cap on an uploaded archive, and on what one restore may write out of it.
BACKUP_MAX_UPLOAD_BYTES = config("BACKUP_MAX_UPLOAD_BYTES", cast=int, default=1024 * 1024 * 1024)
# Automatic backups, every this many hours. 0 turns them off; only automatic
# ones are pruned, and only down to BACKUP_KEEP of them.
BACKUP_INTERVAL_HOURS = config("BACKUP_INTERVAL_HOURS", cast=int, default=0)
BACKUP_KEEP = config("BACKUP_KEEP", cast=int, default=10)
# Dumping and restoring a MySQL/MariaDB database needs its client tools; a
# SQLite database needs nothing at all. Resolved through PATH by default.
MYSQLDUMP_EXECUTABLE_PATH = config("MYSQLDUMP_EXECUTABLE_PATH", default="mysqldump")
MYSQL_EXECUTABLE_PATH = config("MYSQL_EXECUTABLE_PATH", default="mysql")
BACKUP_TIMEOUT = config("BACKUP_TIMEOUT", cast=int, default=600)

# Reverse proxies whose X-Forwarded-For / X-Real-IP headers may be believed,
# as IPs or CIDRs. Empty means the headers are ignored and the peer address is
# used; "*" trusts every peer (only safe when the panel is unreachable directly).
TRUSTED_PROXIES = [
    proxy.strip()
    for proxy in config("TRUSTED_PROXIES", default="", cast=str).split(",")
    if proxy.strip()
]

# Subscription tokens issued before the switch to HMAC signatures are still
# accepted by default so existing links keep working. Set to False once your
# users have picked up freshly issued subscription URLs.
ACCEPT_LEGACY_SUBSCRIPTION_TOKENS = config("ACCEPT_LEGACY_SUBSCRIPTION_TOKENS", default=True, cast=bool)

CUSTOM_TEMPLATES_DIRECTORY = config("CUSTOM_TEMPLATES_DIRECTORY", default=None)
SUBSCRIPTION_PAGE_TEMPLATE = config("SUBSCRIPTION_PAGE_TEMPLATE", default="subscription/index.html")
HOME_PAGE_TEMPLATE = config("HOME_PAGE_TEMPLATE", default="home/index.html")

CLASH_SUBSCRIPTION_TEMPLATE = config("CLASH_SUBSCRIPTION_TEMPLATE", default="clash/default.yml")
CLASH_SETTINGS_TEMPLATE = config("CLASH_SETTINGS_TEMPLATE", default="clash/settings.yml")

SINGBOX_SUBSCRIPTION_TEMPLATE = config("SINGBOX_SUBSCRIPTION_TEMPLATE", default="singbox/default.json")
SINGBOX_SETTINGS_TEMPLATE = config("SINGBOX_SETTINGS_TEMPLATE", default="singbox/settings.json")

MUX_TEMPLATE = config("MUX_TEMPLATE", default="mux/default.json")

V2RAY_SUBSCRIPTION_TEMPLATE = config("V2RAY_SUBSCRIPTION_TEMPLATE", default="v2ray/default.json")
V2RAY_SETTINGS_TEMPLATE = config("V2RAY_SETTINGS_TEMPLATE", default="v2ray/settings.json")

USER_AGENT_TEMPLATE = config("USER_AGENT_TEMPLATE", default="user_agent/default.json")
GRPC_USER_AGENT_TEMPLATE = config("GRPC_USER_AGENT_TEMPLATE", default="user_agent/grpc.json")

EXTERNAL_CONFIG = config("EXTERNAL_CONFIG", default="", cast=str)
LOGIN_NOTIFY_WHITE_LIST = [ip.strip() for ip in config("LOGIN_NOTIFY_WHITE_LIST",
                                                       default="", cast=str).split(",") if ip.strip()]

USE_CUSTOM_JSON_DEFAULT = config("USE_CUSTOM_JSON_DEFAULT", default=False, cast=bool)
USE_CUSTOM_JSON_FOR_V2RAYN = config("USE_CUSTOM_JSON_FOR_V2RAYN", default=False, cast=bool)
USE_CUSTOM_JSON_FOR_V2RAYNG = config("USE_CUSTOM_JSON_FOR_V2RAYNG", default=False, cast=bool)
USE_CUSTOM_JSON_FOR_STREISAND = config("USE_CUSTOM_JSON_FOR_STREISAND", default=False, cast=bool)
USE_CUSTOM_JSON_FOR_HAPP = config("USE_CUSTOM_JSON_FOR_HAPP", default=False, cast=bool)
USE_CUSTOM_JSON_FOR_NPVTUNNEL = config("USE_CUSTOM_JSON_FOR_NPVTUNNEL", default=False, cast=bool)

NOTIFY_STATUS_CHANGE = config("NOTIFY_STATUS_CHANGE", default=True, cast=bool)
NOTIFY_USER_CREATED = config("NOTIFY_USER_CREATED", default=True, cast=bool)
NOTIFY_USER_UPDATED = config("NOTIFY_USER_UPDATED", default=True, cast=bool)
NOTIFY_USER_DELETED = config("NOTIFY_USER_DELETED", default=True, cast=bool)
NOTIFY_USER_DATA_USED_RESET = config("NOTIFY_USER_DATA_USED_RESET", default=True, cast=bool)
NOTIFY_USER_SUB_REVOKED = config("NOTIFY_USER_SUB_REVOKED", default=True, cast=bool)
NOTIFY_IF_DATA_USAGE_PERCENT_REACHED = config("NOTIFY_IF_DATA_USAGE_PERCENT_REACHED", default=True, cast=bool)
NOTIFY_IF_DAYS_LEFT_REACHED = config("NOTIFY_IF_DAYS_LEFT_REACHED", default=True, cast=bool)
NOTIFY_LOGIN = config("NOTIFY_LOGIN", default=True, cast=bool)

ACTIVE_STATUS_TEXT = config("ACTIVE_STATUS_TEXT", default="Active")
EXPIRED_STATUS_TEXT = config("EXPIRED_STATUS_TEXT", default="Expired")
LIMITED_STATUS_TEXT = config("LIMITED_STATUS_TEXT", default="Limited")
DISABLED_STATUS_TEXT = config("DISABLED_STATUS_TEXT", default="Disabled")
ONHOLD_STATUS_TEXT = config("ONHOLD_STATUS_TEXT", default="On-Hold")

USERS_AUTODELETE_DAYS = config("USERS_AUTODELETE_DAYS", default=-1, cast=int)
USER_AUTODELETE_INCLUDE_LIMITED_ACCOUNTS = config("USER_AUTODELETE_INCLUDE_LIMITED_ACCOUNTS", default=False, cast=bool)

# How many devices may fetch one user's subscription, counted by the hardware
# id their client reports. Off by default, and deliberately so: with a limit
# in force a client that sends no identifier at all is refused, and that is
# most of them — a browser opening the subscription page included. A user's
# own hwid_device_limit overrides this, and is NULL until somebody sets one.
USERS_DEFAULT_HWID_DEVICE_LIMIT = config("USERS_DEFAULT_HWID_DEVICE_LIMIT", default=0, cast=int)
# The header the identifier arrives in. Configurable because it is a client
# convention rather than a standard, and conventions move.
HWID_HEADER = config("HWID_HEADER", default="x-hwid")


# USERNAME: PASSWORD
SUDOERS = {config("SUDO_USERNAME"): config("SUDO_PASSWORD")} \
    if config("SUDO_USERNAME", default='') and config("SUDO_PASSWORD", default='') \
    else {}


WEBHOOK_ADDRESS = config(
    'WEBHOOK_ADDRESS',
    default="",
    cast=lambda v: [address.strip() for address in v.split(',')] if v else []
)
WEBHOOK_SECRET = config("WEBHOOK_SECRET", default=None)
# How long to wait on one webhook delivery before giving up. The notification
# job runs on the scheduler, so an address that never answers would hold one of
# its threads indefinitely and let the queue grow behind it. Not the same thing
# as RECURRENT_NOTIFICATIONS_TIMEOUT below, which is the delay between retries.
WEBHOOK_REQUEST_TIMEOUT = config("WEBHOOK_REQUEST_TIMEOUT", cast=int, default=10)

# recurrent notifications

# timeout between each retry of sending a notification in seconds
RECURRENT_NOTIFICATIONS_TIMEOUT = config("RECURRENT_NOTIFICATIONS_TIMEOUT", default=180, cast=int)
# how many times to try after ok response not recevied after sending a notifications
NUMBER_OF_RECURRENT_NOTIFICATIONS = config("NUMBER_OF_RECURRENT_NOTIFICATIONS", default=3, cast=int)

# sends a notification when the user uses this much of thier data
NOTIFY_REACHED_USAGE_PERCENT = config(
    "NOTIFY_REACHED_USAGE_PERCENT",
    default="80",
    cast=lambda v: [int(p.strip()) for p in v.split(',')] if v else []
)

# sends a notification when there is n days left of their service
NOTIFY_DAYS_LEFT = config(
    "NOTIFY_DAYS_LEFT",
    default="3",
    cast=lambda v: [int(d.strip()) for d in v.split(',')] if v else []
)

DISABLE_RECORDING_NODE_USAGE = config("DISABLE_RECORDING_NODE_USAGE", cast=bool, default=False)

# headers: profile-update-interval, support-url, profile-title
SUB_UPDATE_INTERVAL = config("SUB_UPDATE_INTERVAL", default="12")
SUB_SUPPORT_URL = config("SUB_SUPPORT_URL", default="https://t.me/")
SUB_PROFILE_TITLE = config("SUB_PROFILE_TITLE", default="Subscription")

# discord webhook log
DISCORD_WEBHOOK_URL = config("DISCORD_WEBHOOK_URL", default="")
# How long to wait on one Discord delivery before giving up. These are sent
# from the thread that handled the request which triggered them, so a webhook
# that accepts the connection and then never answers would otherwise hold that
# thread for as long as the kernel keeps the socket open.
DISCORD_WEBHOOK_TIMEOUT = config("DISCORD_WEBHOOK_TIMEOUT", cast=int, default=10)


# Interval jobs, all values are in seconds
JOB_CORE_HEALTH_CHECK_INTERVAL = config("JOB_CORE_HEALTH_CHECK_INTERVAL", cast=int, default=10)
JOB_RECORD_NODE_USAGES_INTERVAL = config("JOB_RECORD_NODE_USAGES_INTERVAL", cast=int, default=30)
JOB_RECORD_USER_USAGES_INTERVAL = config("JOB_RECORD_USER_USAGES_INTERVAL", cast=int, default=10)
JOB_REVIEW_USERS_INTERVAL = config("JOB_REVIEW_USERS_INTERVAL", cast=int, default=10)
JOB_SEND_NOTIFICATIONS_INTERVAL = config("JOB_SEND_NOTIFICATIONS_INTERVAL", cast=int, default=30)
