"""Score Gatekeeper and the model-only baseline on the labelled corpus.

Writes eval/results.json, the single source for every number in the README
and the video. A baseline answer counts as catching a bug when one of its
listed problems names that bug (keyword rules below, checked by hand on the
raw answers in eval/runs/). Problems that match no planted bug are counted as
extra findings; on clean cases they are false alarms.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gatekeeper import preflight as pf  # noqa: E402

HERE = Path(__file__).parent

KEYWORDS = {
    "G1": r"enable_?events|enableEvents|events? (are |is )?(not |never )?(enabled|disabled|off)|without events",
    "G2": r"default_flag_handler|defaultFlags|default flag|fallback",
    "G3": r"uuid4?\(|randomUUID|new (random )?(visitor|id|identifier|uuid)|(every|each) (request|page ?load|visit)|random (id|identifier)",
    "G4": r"email|different identif|same identif|mismatch.*identif|identif.*mismatch|not the same (id|identifier)",
    "G5": r"flush|close\(\)|buffer|exits? before|max_buffer_items",
    "G6": r"events?_api_url|eventsApiUrl|events? (api )?(url|endpoint|ingestion)|sen[dt] (the )?events to (flagsmith )?cloud",
    "G9": r"before (calling )?identify|identify\(\).*(after|before)|not (yet )?identified|before the (user|visitor) is identified",
}


def parse_answer(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
        return [i for i in items if isinstance(i, dict)]
    except json.JSONDecodeError:
        return [{"problem": line} for line in text.splitlines() if line.strip()]


def classify(problem: str) -> set[str]:
    return {rule for rule, pat in KEYWORDS.items() if re.search(pat, problem, re.I)}


def main() -> None:
    labels = json.loads((HERE / "cases" / "labels.json").read_text(encoding="utf-8"))
    rows = []
    tot = {"gk": {"tp": 0, "fp_clean": 0}, "base": {"tp": 0, "fp_clean": 0, "answered": 0}}
    planted = sum(len(c["bugs"]) for c in labels)
    clean = [c for c in labels if not c["bugs"]]
    for c in labels:
        bugs = set(c["bugs"])
        gk = {f.rule for f in pf.scan(HERE / "cases" / c["id"]).findings}
        run = HERE / "runs" / f"{c['id']}.json"
        base_rules: set[str] = set()
        base_items: list[dict] = []
        if run.exists():
            ans = json.loads(run.read_text(encoding="utf-8"))
            base_items = parse_answer(ans["answer"])
            tot["base"]["answered"] += 1
            for it in base_items:
                base_rules |= classify(" ".join(str(v) for v in it.values()))
        tot["gk"]["tp"] += len(bugs & gk)
        tot["base"]["tp"] += len(bugs & base_rules)
        if not bugs:
            tot["gk"]["fp_clean"] += 1 if gk else 0
            tot["base"]["fp_clean"] += 1 if base_items else 0
        rows.append({"case": c["id"], "planted": sorted(bugs), "gatekeeper": sorted(gk),
                     "baseline": sorted(base_rules), "baseline_items": len(base_items)})
    per_rule = {}
    for rule in sorted({b for c in labels for b in c["bugs"]}):
        n = sum(rule in r["planted"] for r in rows)
        per_rule[rule] = {"planted": n,
                          "gatekeeper": sum(rule in r["planted"] and rule in r["gatekeeper"] for r in rows),
                          "baseline": sum(rule in r["planted"] and rule in r["baseline"] for r in rows)}
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": {"cases": len(labels), "planted_bugs": planted, "clean_cases": len(clean)},
        "gatekeeper": {"caught": tot["gk"]["tp"], "false_alarm_cases": tot["gk"]["fp_clean"]},
        "baseline": {"model": "Claude Sonnet, fresh agent with the official Flagsmith setup prompt, blind case names",
                     "cases_answered": tot["base"]["answered"], "caught": tot["base"]["tp"],
                     "false_alarm_cases": tot["base"]["fp_clean"]},
        "per_rule": per_rule,
        "rows": rows,
    }
    prev = HERE / "results.json"
    if prev.exists():
        old = json.loads(prev.read_text(encoding="utf-8"))
        for k in ("e2e",):
            if k in old:
                out[k] = old[k]
    prev.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("corpus", "gatekeeper", "baseline", "per_rule")}, indent=2))


if __name__ == "__main__":
    main()
