"""Prepare a fresh self-hosted Flagsmith for the demo (local use only).

Creates an admin user, organisation, project and two environments, turns on
the "Flagsmith database" warehouse in each, and prints the environment
variables Gatekeeper and the demo app need. Run once after `manage.py migrate`.

    python demo/bootstrap_local.py --api http://localhost:8000 > .env.local

The admin password is generated here and only printed to stderr.
"""

from __future__ import annotations

import argparse
import secrets
import sys

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--email", default="demo-admin@example.test")
    args = ap.parse_args()
    base = args.api.rstrip("/") + "/api/v1/"
    h = httpx.Client(base_url=base, timeout=60)
    password = secrets.token_urlsafe(18)
    r = h.post("auth/users/", json={"email": args.email, "password": password, "re_password": password,
                                    "first_name": "Demo", "last_name": "Admin", "sign_up_type": "NO_INVITE"})
    if r.status_code not in (200, 201):
        sys.exit(f"Could not create the admin user ({r.status_code}): {r.text[:300]}")
    token = r.json().get("key") or h.post("auth/login/", json={"email": args.email, "password": password}).json()["key"]
    h.headers["Authorization"] = f"Token {token}"
    org = h.post("organisations/", json={"name": "Gatekeeper demo"}).raise_for_status().json()
    project = h.post("projects/", json={"name": "Signup", "organisation": org["id"]}).raise_for_status().json()
    envs = {}
    for name in ("demo-main", "demo-srm"):
        env = h.post("environments/", json={"name": name, "project": project["id"]}).raise_for_status().json()
        wc = h.post(f"environments/{env['api_key']}/warehouse-connections/",
                    json={"warehouse_type": "postgres", "config": {"source": "flagsmith_database"}})
        if wc.status_code not in (200, 201):
            sys.exit(f"Warehouse connection failed ({wc.status_code}): {wc.text[:300]}")
        envs[name] = env["api_key"]
    print(f"demo admin password (local only): {password}", file=sys.stderr)
    print(f"FLAGSMITH_API_URL={base}")
    print(f"FLAGSMITH_API_KEY={token}")
    print("FLAGSMITH_AUTH_SCHEME=Token")
    print(f"FLAGSMITH_PROJECT_ID={project['id']}")
    print(f"FLAGSMITH_ENV_MAIN={envs['demo-main']}")
    print(f"FLAGSMITH_ENV_SRM={envs['demo-srm']}")


if __name__ == "__main__":
    main()
