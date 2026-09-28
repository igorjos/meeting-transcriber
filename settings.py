"""Saved launcher settings (.env) and the value validators shared with transcribe.py.

The launchers (run_macos.sh / run_windows.ps1) remember the answers that rarely
change between meetings - email, whisper model, the streaming endpoint URL, API
key header - in a `.env` file next to this module, and only ask for what is
missing or what the user chooses to change. The client name / meeting topic and
the store (upload) URL are deliberately not settings: both are asked for every
session and never written anywhere.

The launchers call this module as a tool, so it must stay stdlib-only and fast
to import (no numpy / torch / audio backends):

    python settings.py show                    # list saved values (secrets masked); exit 1 if none
    python settings.py get KEY                 # print the saved value; exit 1 if never saved, 2 if saved but unusable
    SETTINGS_VALUE=... python settings.py set KEY   # validate + save; exit 2 (message on stderr) if invalid

`set` takes the value from the SETTINGS_VALUE environment variable rather than
argv so an API key never shows up in the process list.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

ENV_PATH = Path(__file__).resolve().parent / ".env"
VALUE_ENV = "SETTINGS_VALUE"

EMAIL_KEY = "TRANSCRIBER_EMAIL"
MODEL_KEY = "TRANSCRIBER_MODEL"
URL_KEY = "TRANSCRIBER_URL"
AUTH_HEADER_KEY = "TRANSCRIBER_AUTH_HEADER"

_EMAIL_RE = re.compile(r"^[^\s@|]+@[^\s@|]+\.[^\s@|]+$")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_QUOTES = ("'", '"')


def validate_url(url: Optional[str], flag: str = "--url") -> Optional[str]:
    """Return an error message if `url` is missing an http(s) scheme, else None."""
    if url and urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return f"{flag} must start with http:// or https:// (got {url!r})"
    return None


def parse_auth_header(raw: Optional[str], flag: str = "--auth-header") -> "dict[str, str]":
    """Parse 'Name: value' into a one-entry header dict ({} if `raw` is empty).
    Raises ValueError with a user-facing message if it isn't a valid header."""
    if not raw:
        return {}
    name, sep, value = raw.partition(":")
    name, value = name.strip(), value.strip()
    if not sep or not name or not value:
        raise ValueError(f"{flag} must look like 'Name: value' (e.g. 'Authorization: Bearer <token>')")
    if not _HEADER_NAME_RE.match(name):
        raise ValueError(f"{flag} name {name!r} is not a valid HTTP header name")
    if any(ch in value for ch in "\r\n\0"):
        raise ValueError(f"{flag} value must not contain line breaks")
    return {name: value}


def validate_email(email: Optional[str], flag: str = "--email") -> Optional[str]:
    """Return an error message if `email` is not a plausible single address, else None.
    Deliberately loose (local@domain.tld): it is a label in the log, not a delivery address."""
    if email and not _EMAIL_RE.match(email):
        return f"{flag} must look like name@example.com (got {email!r})"
    return None


def _validate_required(value: str, label: str) -> Optional[str]:
    return None if value else f"{label} must not be empty"


def _validate_auth_header(value: str) -> Optional[str]:
    try:
        parse_auth_header(value, "API key header")
    except ValueError as exc:
        return str(exc)
    return None


@dataclass(frozen=True)
class Setting:
    label: str
    validate: Callable[[str], Optional[str]]
    secret: bool = False


# Order is the order `show` lists them in. Only these keys can be read or written.
SETTINGS: Dict[str, Setting] = {
    EMAIL_KEY: Setting("Email", lambda v: _validate_required(v, "Email") or validate_email(v, "Email")),
    MODEL_KEY: Setting("Model", lambda v: _validate_required(v, "Model")),
    URL_KEY: Setting("Stream URL", lambda v: _validate_required(v, "URL") or validate_url(v, "URL")),
    # Empty is valid here: it records "asked, the endpoint needs no key" so the launcher stops asking.
    AUTH_HEADER_KEY: Setting("API key", _validate_auth_header, secret=True),
}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return value[1:-1]
    return value


def _parse_line(line: str) -> Optional["tuple[str, str]"]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    key, sep, value = stripped.partition("=")
    key = key.strip()
    if not sep or not key:
        return None
    return key, _unquote(value.strip())


def load(path: Path) -> Dict[str, str]:
    """Return every KEY=value in the file (last duplicate wins). {} if the file doesn't exist.
    Whole-line `#` comments only - a `#` inside a value is part of the value."""
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = _parse_line(line)
        if parsed:
            values[parsed[0]] = parsed[1]
    return values


def check_value(key: str, value: str) -> Optional[str]:
    """Return an error message if `value` can't be saved under `key`, else None."""
    setting = SETTINGS.get(key)
    if setting is None:
        return f"unknown setting {key!r} (known: {', '.join(SETTINGS)})"
    if not (value.isascii() and value.isprintable()):
        return f"{setting.label} must be a single line of printable ASCII characters"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return f"{setting.label} must not start and end with a quote character"
    return setting.validate(value)


def save(key: str, value: str, path: Path) -> None:
    """Validate and write KEY=value, keeping every other line of the file as it was.
    Raises ValueError if the value is invalid. The file is replaced atomically and
    created owner-only (0600 on POSIX) before any secret is written to it."""
    value = _unquote(value.strip())
    error = check_value(key, value)
    if error:
        raise ValueError(error)

    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    replaced = False
    for index in range(len(lines) - 1, -1, -1):
        parsed = _parse_line(lines[index])
        if parsed and parsed[0] == key:
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)  # a stale one from a crash may have looser permissions than 0600
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def mask(key: str, value: str) -> str:
    """Display form of a saved value: secrets never show more than the last 4 characters."""
    if not SETTINGS[key].secret:
        return value
    if not value:
        return "(none)"
    name, sep, secret = value.partition(":")
    secret = secret.strip()
    if not sep:
        return "****"
    tail = secret[-4:] if len(secret) >= 12 else ""
    return f"{name.strip()}: ****{tail}"


def describe(values: Dict[str, str]) -> "list[str]":
    """One 'Label: value' line per saved setting, in SETTINGS order."""
    width = max(len(setting.label) for setting in SETTINGS.values()) + 1
    return [
        f"  {(SETTINGS[key].label + ':').ljust(width)} {mask(key, values[key])}"
        for key in SETTINGS
        if key in values
    ]


def main(argv: Optional["list[str]"] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else ""

    if command == "show" and len(args) == 1:
        lines = describe(load(ENV_PATH))
        if not lines:
            return 1
        print(f"Saved settings ({ENV_PATH.name}):")
        print("\n".join(lines))
        return 0

    if command == "get" and len(args) == 2:
        if args[1] not in SETTINGS:
            print(f"ERROR: unknown setting {args[1]!r}", file=sys.stderr)
            return 2
        values = load(ENV_PATH)
        if args[1] not in values:
            return 1
        error = check_value(args[1], values[args[1]])
        if error:
            print(f"Saved {SETTINGS[args[1]].label} is not usable ({error}) - please enter it again.", file=sys.stderr)
            return 2
        print(values[args[1]])
        return 0

    if command == "set" and len(args) == 2:
        try:
            save(args[1], os.environ.get(VALUE_ENV, ""), ENV_PATH)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    print("usage: python settings.py show | get KEY | set KEY (value in $SETTINGS_VALUE)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
