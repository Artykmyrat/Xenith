"""Reading and applying kernel tunables from the panel.

Live values are read straight out of /proc/sys. Changes are written to one
file the panel owns under /etc/sysctl.d and then applied with sysctl(8), so a
change survives a reboot and takes effect without one.

Two things keep this narrow. Only keys in the catalogue may be touched, and
every value is matched against the shape its key expects before it reaches
the file — sysctl.d files are read by init, so a value containing a newline
would be a way to set anything at all.

Off by default: it needs a writable /proc/sys and root, which a container
does not have unless it was started for it.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.utils.files import FileWriteError, atomic_write
from app.utils.sysctl_catalog import BASELINE, BY_KEY, TUNABLES, Tunable, section_titles
from config import (SYSCTL_CONF_PATH, SYSCTL_ENABLED, SYSCTL_EXECUTABLE_PATH,
                    SYSCTL_PROC_PATH, SYSCTL_TIMEOUT)

VALUE_PATTERNS = {
    "int": re.compile(r"^-?\d{1,19}$"),
    "ints": re.compile(r"^-?\d{1,19}( -?\d{1,19}){0,7}$"),
    "text": re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,31}$"),
}

# How sysctl(8) names the key it could not set. Which shape comes out depends
# on the procps version and on how the key failed, and a shape that is not
# matched here costs the whole set: the caller cannot attribute the failure to
# one key, so it raises for all of them.
#
#   sysctl: setting key "net.core.somaxconn": Read-only file system
#   sysctl: permission denied on key "net.ipv4.ip_forward"
_FAILURE_RES = (
    re.compile(r'''setting key ["']([^"']+)["']:\s*(.+)$'''),
    re.compile(r'''permission denied on key ["']([^"']+)["']'''),
)

# sysctl: cannot stat /proc/sys/net/bridge/bridge-nf-call-iptables: No such file or directory
#
# A key below /proc/sys, rather than /proc/sys itself, which the kernel does
# not expose. Its own line so it can be reported as the ordinary thing it is.
_MISSING_RE = re.compile(r"cannot stat /proc/sys/(\S+?):\s*(.+)$")

# The proc path is what sysctl names in that case; the catalogue turns it back
# into the key an admin recognises.
_KEY_BY_PROC_PATH = {tunable.proc_path: tunable.key for tunable in TUNABLES}

MISSING_KEY = (
    "This kernel does not expose the parameter — usually because the module "
    "providing it is not loaded. It stays in the managed file and takes effect "
    "if the module appears."
)

# Said about a key the catalogue already knows is module-backed. The same
# situation as MISSING_KEY, but an expected one rather than a surprise, which
# is why it is reported apart from the refusals: a host with no br_netfilter
# loaded is an ordinary host, and warning about it on every save trains an
# admin to ignore the one warning that will matter.
MODULE_ABSENT = (
    "Waiting for the {module} module. The value is in the managed file and the "
    "kernel takes it up as soon as the module loads."
)

HEADER = (
    "# Managed by Xenith. Edited from the panel's System settings screen;\n"
    "# anything written here by hand is replaced on the next save.\n"
)


class SysctlError(Exception):
    """The request could not be carried out; the message is safe to show."""


@dataclass
class ApplyResult:
    applied: Dict[str, str]
    failed: List[Tuple[str, str]] = field(default_factory=list)
    # Keys the kernel does not expose because their module is not loaded. Not
    # failures: the value is written and waiting, and nothing is wrong with the
    # host. Kept apart from `failed` so the dashboard can say so quietly.
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def is_enabled() -> bool:
    return bool(SYSCTL_ENABLED)


def conf_path() -> str:
    """The file the panel owns. Read through here so there is one source of truth."""
    return SYSCTL_CONF_PATH


def _proc_path(tunable: Tunable) -> str:
    return os.path.join(SYSCTL_PROC_PATH, tunable.proc_path)


def read_value(tunable: Tunable) -> Optional[str]:
    """The live value of one tunable, or None when the kernel does not expose it.

    A key can be missing for ordinary reasons: nf_conntrack only appears once
    the module is loaded, and per-interface keys only once the interface is.
    """
    try:
        with open(_proc_path(tunable), "r") as handle:
            return " ".join(handle.read().split())
    except (FileNotFoundError, PermissionError, OSError):
        return None


def read_values() -> Dict[str, Optional[str]]:
    return {tunable.key: read_value(tunable) for tunable in TUNABLES}


def writable() -> Tuple[bool, Optional[str]]:
    """Whether changes can be written, and why not when they cannot.

    Checked before anything is written so the dashboard can explain itself
    instead of showing a failure per key after the fact.
    """
    if not is_enabled():
        return False, (
            "Kernel tuning is disabled. Set SYSCTL_ENABLED = True in the panel's .env "
            "and run `xenith restart`, which brings the container back with the "
            "privileges this needs."
        )

    if not os.path.isdir(SYSCTL_PROC_PATH):
        return False, f"{SYSCTL_PROC_PATH} is not available in this environment."

    probe = os.path.join(SYSCTL_PROC_PATH, "net/ipv4/ip_forward")
    if os.path.exists(probe) and not os.access(probe, os.W_OK):
        return False, (
            "/proc/sys is mounted read-only, so the container was started without the "
            "privileges kernel tuning needs. Run `xenith restart` to recreate it."
        )

    directory = os.path.dirname(SYSCTL_CONF_PATH)
    if not os.path.isdir(directory):
        return False, f"{directory} does not exist, so the change could not be made persistent."
    if not os.access(directory, os.W_OK):
        return False, f"{directory} is not writable by the panel."

    return True, None


def validate(key: str, value) -> str:
    """Normalise one key/value pair, rejecting anything not in the catalogue."""
    tunable = BY_KEY.get(key)
    if tunable is None:
        raise SysctlError(f"{key!r} is not a setting this panel manages.")

    if value is None:
        raise SysctlError(f"{key} needs a value.")

    normalised = " ".join(str(value).split())
    if not normalised:
        raise SysctlError(f"{key} needs a value.")

    if not VALUE_PATTERNS[tunable.kind].match(normalised):
        raise SysctlError(f"{normalised!r} is not a valid value for {key}.")

    return normalised


def validate_many(values: Dict[str, object]) -> Dict[str, str]:
    if not values:
        raise SysctlError("No settings were given.")
    return {key: validate(key, value) for key, value in values.items()}


def render(values: Dict[str, str]) -> str:
    """The sysctl.d file for a set of values, in catalogue order."""
    lines = [HEADER]
    for section, title in section_titles():
        keys = [t.key for t in TUNABLES if t.section == section and t.key in values]
        if not keys:
            continue
        lines.append(f"\n# {title}\n")
        lines.extend(f"{key} = {values[key]}\n" for key in keys)
    return "".join(lines)


def _write_conf(content: str) -> None:
    try:
        atomic_write(SYSCTL_CONF_PATH, content)
    except FileWriteError as err:
        raise SysctlError(str(err))


def _parse_failures(output: str) -> List[Tuple[str, str]]:
    failures = []
    for line in (output or "").splitlines():
        line = line.strip()

        missing = _MISSING_RE.search(line)
        if missing:
            path = missing.group(1)
            failures.append((_KEY_BY_PROC_PATH.get(path, path.replace("/", ".")), MISSING_KEY))
            continue

        for pattern in _FAILURE_RES:
            match = pattern.search(line)
            if match:
                reason = match.group(2).strip() if match.lastindex > 1 else "Permission denied."
                failures.append((match.group(1), reason))
                break
    return failures


def _load_conf() -> List[Tuple[str, str]]:
    """Apply the managed file, returning the keys the kernel refused.

    A refusal is per key and does not undo the rest: a container without
    CAP_SYS_ADMIN can set some parameters and not others, and the caller is
    better served by a list of what did not take than by one opaque error.
    """
    try:
        result = subprocess.run(
            [SYSCTL_EXECUTABLE_PATH, "-p", SYSCTL_CONF_PATH],
            capture_output=True,
            text=True,
            timeout=SYSCTL_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise SysctlError(f"sysctl was not found at {SYSCTL_EXECUTABLE_PATH}.")
    except subprocess.TimeoutExpired:
        raise SysctlError(f"sysctl did not finish within {SYSCTL_TIMEOUT} seconds.")
    except PermissionError:
        raise SysctlError("The panel is not allowed to run sysctl. It needs root privileges.")

    failures = _parse_failures(result.stderr)
    if result.returncode != 0 and not failures:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        raise SysctlError(message[-1][:400] if message else "sysctl failed without an error message.")

    return failures


def _split_failures(failures: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Separate the refusals worth warning about from the ones that are routine.

    A key the catalogue marks as module-backed, which the kernel is not
    exposing, has not been refused in any meaningful sense — the module simply
    is not loaded. Reporting that as a failure alongside a genuine permission
    problem makes the genuine one harder to see.
    """
    failed, skipped = [], []

    for key, message in failures:
        tunable = BY_KEY.get(key)
        if tunable is not None and tunable.module and read_value(tunable) is None:
            skipped.append((key, MODULE_ABSENT.format(module=tunable.module)))
        else:
            failed.append((key, message))

    return failed, skipped


def apply(values: Dict[str, object]) -> ApplyResult:
    """Validate, persist and apply a full set of values."""
    allowed, reason = writable()
    if not allowed:
        raise SysctlError(reason)

    validated = validate_many(values)
    _write_conf(render(validated))
    failed, skipped = _split_failures(_load_conf())

    untaken = {key for key, _ in failed} | {key for key, _ in skipped}
    return ApplyResult(
        applied={key: value for key, value in validated.items() if key not in untaken},
        failed=failed,
        skipped=skipped,
    )


def managed_values() -> Dict[str, str]:
    """What the managed file currently asks for, ignoring anything unrecognised."""
    values = {}
    try:
        with open(SYSCTL_CONF_PATH, "r") as handle:
            for line in handle:
                line = line.split("#", 1)[0].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key in BY_KEY:
                    values[key] = " ".join(value.split())
    except (FileNotFoundError, PermissionError, OSError):
        return {}
    return values


def effective_values() -> Dict[str, str]:
    """The values to show as current: live where the kernel exposes them.

    Falls back to the managed file and then to the baseline, so a key whose
    module is not loaded still shows what it is set to rather than a blank.
    """
    managed = managed_values()
    resolved = {}
    for tunable in TUNABLES:
        live = read_value(tunable)
        resolved[tunable.key] = live if live is not None else managed.get(tunable.key, BASELINE[tunable.key])
    return resolved
