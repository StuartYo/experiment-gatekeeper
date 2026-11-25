"""Screenshots of the console in replay mode (used for README and design review).

    python docs/shoot.py --url http://127.0.0.1:4811/ --out <dir>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

SIZES = {"1440": (1440, 900), "1920": (1920, 945), "1366": (1366, 768), "390": (390, 844)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:4811/")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        for name, (w, h) in SIZES.items():
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2 if name == "390" else 1)
            pg.goto(args.url)
            pg.wait_for_selector(".track")
            pg.wait_for_timeout(600)
            # second experiment: step through every frame and shoot key stages
            pg.click("#exp-list li:nth-child(1) button")
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(out / f"{name}_main_preflight.png"), full_page=True)
            for i in range(14):
                btn = pg.query_selector(".stage .actions button")
                if not btn or "Replay from the start" in (btn.inner_text() or ""):
                    break
                label = btn.inner_text()
                btn.click()
                if pg.is_visible("#approve-dlg"):
                    slug = pg.get_attribute("#dlg-name", "placeholder")
                    pg.fill("#dlg-name", slug)
                    pg.click("#dlg-ok")
                pg.wait_for_timeout(700)
                safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:40]
                pg.screenshot(path=str(out / f"{name}_main_{i + 1:02d}_{safe}.png"), full_page=True)
            pg.click("#exp-list li:nth-child(2) button")
            pg.wait_for_timeout(300)
            pg.click(".stage .actions button")
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(out / f"{name}_srm_verdict.png"), full_page=True)
            pg.close()
        b.close()
    print("shots in", out)


if __name__ == "__main__":
    main()
