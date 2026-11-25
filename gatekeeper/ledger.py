"""Local record of every experiment Gatekeeper manages, and the approvals.

One JSON file per experiment in ``.gk/experiments/``. Approvals are signed
with a key that lives in the approver's home directory (never in the repo), and
they are bound to a fingerprint of the live Flagsmith setup. If anyone (or any
agent) changes the flag, split or metric after a person approved it, the
fingerprint changes and the approval no longer counts.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "experiment"


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def home() -> Path:
    root = Path(os.environ.get("GK_HOME", ".gk"))
    (root / "experiments").mkdir(parents=True, exist_ok=True)
    return root


def approver_key_path() -> Path:
    return Path(os.environ.get("GK_APPROVER_KEY", Path.home() / ".gatekeeper" / "approver.key"))


def approver_key(create: bool = False) -> bytes | None:
    p = approver_key_path()
    if p.exists():
        return p.read_bytes().strip()
    if not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    p.write_bytes(key)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


def sign(kind: str, slug: str, fingerprint: str, by: str, at: str) -> str | None:
    key = approver_key()
    if key is None:
        return None
    msg = f"{kind}|{slug}|{fingerprint}|{by}|{at}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def whoami() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


class Record:
    def __init__(self, slug: str, data: dict | None = None):
        self.slug = slug
        self.path = home() / "experiments" / f"{slug}.json"
        self.data = data if data is not None else json.loads(self.path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ persistence
    @classmethod
    def create(cls, name: str, **fields) -> "Record":
        slug = slugify(name)
        rec = cls(slug, {"name": name, "slug": slug, "created_at": now(), "status": "draft",
                         "approvals": [], "requests": [], "peeks": [], "verdicts": [], "timeline": [], **fields})
        rec.save()
        return rec

    @classmethod
    def load(cls, slug: str) -> "Record":
        path = home() / "experiments" / f"{slug}.json"
        if not path.exists():
            raise FileNotFoundError(f"No experiment called {slug!r}. Run `gk list` to see them.")
        return cls(slug)

    @classmethod
    def all(cls) -> list["Record"]:
        out = []
        for p in sorted((home() / "experiments").glob("*.json")):
            out.append(cls(p.stem))
        return sorted(out, key=lambda r: r.data.get("created_at", ""), reverse=True)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def log(self, actor: str, action: str, detail: str = "") -> None:
        self.data.setdefault("timeline", []).append({"at": now(), "actor": actor, "action": action, "detail": detail})
        self.save()

    # ------------------------------------------------------------ approvals
    def request(self, kind: str, by: str, note: str = "") -> None:
        self.data["requests"].append({"kind": kind, "at": now(), "by": by, "note": note, "open": True})
        if kind == "start":
            self.data["status"] = "awaiting_approval"
        elif kind == "ship":
            self.data["status"] = "awaiting_ship_approval"
        self.log(by, f"requested {kind} approval", note)

    def approve(self, kind: str, fingerprint: str, via: str, by: str | None = None) -> dict:
        approver_key(create=True)
        by = by or whoami()
        at = now()
        entry = {"kind": kind, "fingerprint": fingerprint, "by": by, "at": at, "via": via,
                 "signature": sign(kind, self.slug, fingerprint, by, at)}
        self.data["approvals"].append(entry)
        for r in self.data["requests"]:
            if r["kind"] == kind:
                r["open"] = False
        if kind == "start":
            self.data["status"] = "approved"
        self.log(f"person:{by}", f"approved {kind}", f"via {via}, fingerprint {fingerprint[:12]}")
        return entry

    def valid_approval(self, kind: str, fingerprint: str) -> dict | None:
        for a in reversed(self.data["approvals"]):
            if a["kind"] != kind:
                continue
            expected = sign(kind, self.slug, a["fingerprint"], a["by"], a["at"])
            if expected is None or not hmac.compare_digest(expected, a.get("signature") or ""):
                continue
            if a["fingerprint"] == fingerprint:
                return a
        return None

    def latest_approval(self, kind: str) -> dict | None:
        for a in reversed(self.data["approvals"]):
            if a["kind"] == kind:
                return a
        return None
