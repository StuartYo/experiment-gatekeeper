"""Simulated visitors for the demo (all traffic here is SIMULATED, not real users).

Runs the real demo Flask app in-process with its test client, so the app's own
Flagsmith calls record exposures and conversions in Flagsmith exactly as they
would for a browser.

Scenarios
  healthy  every visitor keeps a cookie; the short form converts better
  early    the same app, but stopped after a few hundred visitors
  srm      a heavy script on the control page makes some control visitors
           leave before the experiment flag is read, so exposures go 58/42
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import random
import sys
import time
from pathlib import Path


def load_app(path: Path):
    spec = importlib.util.spec_from_file_location(f"demo_app_{path.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default=str(Path(__file__).parent / "signup_fixed" / "app.py"))
    ap.add_argument("--visitors", type=int, default=2000)
    ap.add_argument("--base", type=float, default=0.30, help="control signup rate")
    ap.add_argument("--lift", type=float, default=0.15, help="relative lift of the short form")
    ap.add_argument("--scenario", choices=["healthy", "early", "srm"], default="healthy")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prefix", default="sim")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    mod = load_app(Path(args.app))
    app, fs = mod.app, mod.flagsmith
    client = app.test_client()
    seen = {"control": 0, "short": 0}
    conv = {"control": 0, "short": 0}
    left = 0
    t0 = time.time()
    for i in range(args.visitors):
        vid = f"{args.prefix}-{args.seed}-{i:06d}"
        client.set_cookie("vid", vid)
        if args.scenario == "srm":
            # The control page is slow: 28% of control visitors leave before the
            # experiment flag call records their exposure.
            variant = str(fs.get_identity_flags(vid).get_feature_value("signup_form"))
            if variant == "control" and rng.random() < 0.28:
                left += 1
                continue
        html = client.get("/").get_data(as_text=True)
        variant = "short" if html.count("<input") == 3 else "control"
        seen[variant] += 1
        rate = args.base * (1 + args.lift) if variant == "short" else args.base
        if rng.random() < rate:
            client.post("/signup", data={"email": f"{vid}@example.test", "password": "x", "name": "x"})
            conv[variant] += 1
        if i and i % 500 == 0:
            print(f"  {i} visitors...", file=sys.stderr)
    try:
        fs._event_processor.flush() if hasattr(fs, "_event_processor") else None  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    for closer in ("close", "flush_events"):
        if hasattr(fs, closer):
            try:
                getattr(fs, closer)()
            except Exception:  # noqa: BLE001
                pass
    print(f"SIMULATED {args.scenario}: shown control={seen['control']} short={seen['short']}, "
          f"signups control={conv['control']} short={conv['short']}, left before exposure={left}, "
          f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    os.environ.setdefault("FLAGSMITH_SDK_URL", "http://localhost:8000/api/v1/")
    main()
