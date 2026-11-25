"""Build the labelled evaluation corpus: small apps with known experiment bugs.

Each case is a folder in eval/cases/<id>/ with one app file and a label in
eval/cases/labels.json. Bugs are switched on and off independently so every
rule appears both alone and together with others, and clean cases measure
false alarms. Deterministic: run it twice, get the same files.
"""

from __future__ import annotations

import json
import shutil
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).parent / "cases"

PY = '''import os
import uuid

from flagsmith import EventProcessorConfig, Flagsmith
from flagsmith.models import DefaultFlag
from flask import Flask, request, make_response

app = Flask(__name__)
API = "{api}"
flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
{client}
)


def current_visitor():
    return request.cookies.get("vid") or str(uuid.uuid4())


@app.get("/pricing")
def pricing():
{ident}
    flag = flagsmith.get_experiment_flag("{flag}", identifier=vid)
    resp = make_response(f"<h1>{{flag.value}}</h1><a href=/buy>Buy</a>")
    resp.set_cookie("vid", vid)
    return resp


@app.post("/buy")
def buy():
    user_email = request.form.get("email", "")
    flagsmith.track_event("{event}", identifier={conv_ident})
    return "thanks"
'''

PY_JOB = '''"""Nightly job that replays queued checkouts as conversion events."""
import os

from flagsmith import EventProcessorConfig, Flagsmith
from flagsmith.models import DefaultFlag

flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
    enable_events=True,
    default_flag_handler=lambda name: DefaultFlag(enabled=True, value="control"),
{buffer}
)


def queued():
    return [("visitor-1", 1), ("visitor-2", 0)]


if __name__ == "__main__":
    for visitor, bought in queued():
        flagsmith.get_experiment_flag("{flag}", identifier=visitor)
        if bought:
            flagsmith.track_event("{event}", identifier=visitor)
{close}
'''

JS = '''import flagsmith from '@flagsmith/flagsmith';

await flagsmith.init({{
  environmentID: import.meta.env.VITE_FLAGSMITH_KEY,
{init}
}});

{ident}
{order}

document.querySelector('#buy').addEventListener('click', () => {{
  flagsmith.trackEvent('{event}');
}});
'''


def py_case(bugs: set[str]) -> str:
    self_hosted = "G6" in bugs or "selfhost" in bugs
    api = "https://flags.internal.example/api/v1/" if self_hosted else "https://edge.api.flagsmith.com/api/v1/"
    client = []
    if self_hosted:
        client.append("    api_url=API,")
    if "G1" not in bugs:
        client.append("    enable_events=True,")
    if self_hosted and "G6" not in bugs:
        client.append('    event_processor_config=EventProcessorConfig(events_api_url="https://flags.internal.example/api/"),')
    if "G2" not in bugs:
        client.append('    default_flag_handler=lambda name: DefaultFlag(enabled=True, value="control"),')
    ident = "    vid = str(uuid.uuid4())" if "G3" in bugs else "    vid = current_visitor()"
    conv = "user_email" if "G4" in bugs else "current_visitor()"
    return PY.format(api=api, client="\n".join(client), ident=ident, flag="pricing_page", event="checkout", conv_ident=conv)


def py_job(bugs: set[str]) -> str:
    flush = "G5" not in bugs
    return PY_JOB.format(flag="pricing_page", event="checkout",
                         buffer="    event_processor_config=EventProcessorConfig(max_buffer_items=1)," if flush else "",
                         close="")


def js_case(bugs: set[str]) -> str:
    init = []
    if "G1" not in bugs:
        init.append("  enableEvents: true,")
    if "G2" not in bugs:
        init.append("  defaultFlags: { hero_copy: { enabled: true, value: 'control' } },")
    if "G6" in bugs or "selfhost" in bugs:
        init.append("  api: 'https://flags.internal.example/api/v1/',")
        if "G6" not in bugs:
            init.append("  eventProcessorConfig: { eventsApiUrl: 'https://events.internal.example/' },")
    ident = "const visitor = crypto.randomUUID();" if "G3" in bugs else \
        "const visitor = localStorage.getItem('vid') ?? crypto.randomUUID();\nlocalStorage.setItem('vid', visitor);"
    if "G9" in bugs:
        order = "const flag = flagsmith.getExperimentFlag('hero_copy');\nawait flagsmith.identify(visitor);"
    else:
        order = "await flagsmith.identify(visitor);\nconst flag = flagsmith.getExperimentFlag('hero_copy');"
    order += "\ndocument.querySelector('h1').textContent = flag?.value ?? 'Plans that scale';"
    return JS.format(init="\n".join(init), ident=ident, order=order, event="checkout")


def build() -> list[dict]:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    labels = []

    def add(kind: str, bugs: set[str], text: str, fname: str):
        cid = f"{kind}-{len(labels):02d}-" + ("-".join(sorted(b for b in bugs if b.startswith('G'))) or "clean")
        (ROOT / cid).mkdir()
        (ROOT / cid / fname).write_text(text, encoding="utf-8")
        labels.append({"id": cid, "kind": kind, "file": fname, "bugs": sorted(b for b in bugs if b.startswith("G"))})

    py_bugs = ["G1", "G2", "G3", "G4", "G6"]
    for b in py_bugs:
        add("py", {b}, py_case({b}), "app.py")
    for a, b in [("G1", "G3"), ("G3", "G4"), ("G4", "G6"), ("G1", "G6")]:
        add("py", {a, b}, py_case({a, b}), "app.py")
    add("py", set(), py_case(set()), "app.py")
    add("py", {"selfhost"}, py_case({"selfhost"}), "app.py")
    add("pyjob", {"G5"}, py_job({"G5"}), "job.py")
    add("pyjob", set(), py_job(set()), "job.py")
    for b in ["G1", "G2", "G3", "G6", "G9"]:
        add("js", {b}, js_case({b}), "main.js")
    for a, b in [("G3", "G9"), ("G1", "G6")]:
        add("js", {a, b}, js_case({a, b}), "main.js")
    add("js", set(), js_case(set()), "main.js")
    add("js", {"selfhost"}, js_case({"selfhost"}), "main.js")
    (ROOT / "labels.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
    return labels


if __name__ == "__main__":
    ls = build()
    print(f"{len(ls)} cases, {sum(len(x['bugs']) for x in ls)} planted bugs, {sum(1 for x in ls if not x['bugs'])} clean")
