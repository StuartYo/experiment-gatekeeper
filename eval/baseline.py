"""Model-only baseline: Flagsmith's official coding-agent setup prompt, run by
Claude Code (headless) on each case, asked to review the existing code.

The prompt is fetched from the Flagsmith docs at run time and not stored in
this repo. Raw answers go to eval/runs/ (git-ignored); the scored summary goes
to eval/results.json via score.py.

The baseline is given more help than the setup prompt normally gives: we
answer its step-1 questions and explicitly ask it to list instrumentation
problems before starting.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).parent
RUNS = HERE / "runs"
URL = "https://docs.flagsmith.com/experimentation/set-up-with-a-coding-agent"


def official_prompt() -> str:
    page = httpx.get(URL, timeout=30, follow_redirects=True).text
    m = re.search(r"Help me run an A/B test in this codebase.*?(?=After it runs)", re.sub(r"<[^>]+>", " ", page), re.S)
    if not m:
        sys.exit("Could not find the prompt on the docs page.")
    return re.sub(r"[ \t]+", " ", html.unescape(m.group(0))).strip()


def task(case: dict, prompt: str) -> str:
    flag = "hero_copy" if case["kind"] == "js" else "pricing_page"
    return (prompt + "\n\n---\nAnswers to step 1: we want to test the flag `" + flag + "`; success is the `checkout` event; "
            "we use Flagsmith Cloud unless the code points elsewhere. The SDK calls are ALREADY in this codebase. "
            "Do not edit files and do not call Flagsmith. Before we start the experiment, review the existing code "
            "and list every problem that would make exposures or conversions be recorded wrongly or not at all. "
            'Reply with only a JSON array of objects {"file": ..., "line": ..., "problem": ...}; reply [] if there are none.')


def run_one(case: dict, prompt: str, model: str) -> dict:
    out = RUNS / f"{case['id']}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    cwd = HERE / "cases" / case["id"]
    exe = shutil.which("claude") or "claude"
    r = subprocess.run([exe, "-p", "--model", model, "--allowedTools", "Read,Glob,Grep", "--output-format", "text"],
                       input=task(case, prompt), cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=600)
    rec = {"id": case["id"], "model": model, "answer": r.stdout.strip(), "stderr": r.stderr[-500:]}
    out.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    print(f"done {case['id']}", flush=True)
    return rec


def main(model: str = "sonnet") -> None:
    RUNS.mkdir(exist_ok=True)
    prompt = official_prompt()
    (RUNS / "_prompt_fetched.txt").write_text(prompt, encoding="utf-8")
    cases = json.loads((HERE / "cases" / "labels.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda c: run_one(c, prompt, model), cases))


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
