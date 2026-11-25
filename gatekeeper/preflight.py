"""Static checks that run before an experiment is allowed to start.

Each rule comes from a way an experiment can silently produce wrong numbers
with the Flagsmith SDKs. The rules look at source code only; checks that need
the live Flagsmith project (variation keys, metric event names) live in
``live_checks`` and take the API responses as plain dicts, so they are easy to
test offline.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".gk", ".next"}
PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

RULES: dict[str, dict[str, str]] = {
    "G1": {
        "title": "Events are not enabled",
        "why": "The SDK only records exposures and conversions when events are enabled. Without it the experiment shows zero visitors.",
        "fix": "Create the client with enable_events=True (Python) or enableEvents: true (JavaScript).",
    },
    "G2": {
        "title": "No default flag handler",
        "why": "When Flagsmith cannot be reached, a client without a default handler has no value to serve, so the page can break or render nothing for some visitors.",
        "fix": "Pass default_flag_handler (Python) or defaultFlags (JavaScript) with the control value.",
    },
    "G3": {
        "title": "A new visitor ID is made on every request",
        "why": "Bucketing is deterministic on the identifier. A fresh random ID per request puts the same person in a different group on every page view and counts them many times.",
        "fix": "Create the ID once, store it (cookie, local storage, session or user ID) and reuse it.",
    },
    "G4": {
        "title": "Exposure and conversion use different identifiers",
        "why": "Flagsmith joins exposures and conversions per identity. If they are sent under different IDs, no conversion is ever matched to a variation.",
        "fix": "Send track_event with the same identifier that was passed to get_experiment_flag.",
    },
    "G5": {
        "title": "Buffered events are dropped when the script exits",
        "why": "The SDK sends events in batches every few seconds. A short-lived script or job that exits first loses the last batch.",
        "fix": "Python: EventProcessorConfig(max_buffer_items=1) or call close(). JavaScript: await flagsmith.flushEvents() / await flagsmith.close().",
    },
    "G6": {
        "title": "Self-hosted API but events still go to Flagsmith Cloud",
        "why": "Setting the API URL does not move events. Without an events URL the SDK posts events to Cloud and the self-hosted warehouse never sees them.",
        "fix": "Set event_processor_config=EventProcessorConfig(events_api_url=...) (Python) or eventProcessorConfig.eventsApiUrl (JavaScript).",
    },
    "G7": {
        "title": "A variation has no key",
        "why": "A variation without a key records no exposures, so it never shows up in the results.",
        "fix": "flagsmith feature variant add <flag> --value <value> --key <key>, or set the key on the variation in the dashboard.",
    },
    "G8": {
        "title": "Metric event name does not match the code",
        "why": "Event names are case-sensitive. If the metric listens for an event the code never sends, the metric reads zero for every group.",
        "fix": "Use the exact event name from track_event in the metric definition, or rename the event in code.",
    },
    "G9": {
        "title": "Experiment flag is read before the user is identified",
        "why": "In the browser SDK getExperimentFlag needs an identity. Called before identify(), the exposure is not recorded for that visitor.",
        "fix": "await flagsmith.identify(userId) before calling getExperimentFlag.",
    },
}

SEVERITY = {"G1": "block", "G3": "block", "G4": "block", "G6": "block", "G7": "block", "G8": "block",
            "G2": "warn", "G5": "block", "G9": "block"}

RANDOM_CALLS = {"uuid4", "uuid1", "token_hex", "token_urlsafe", "random", "randint", "getrandbits", "time", "time_ns", "now"}
PERSIST_HINTS = ("cookie", "cookies", "session", "localstorage", "local_storage", "user", "account", "request.args", "header")


@dataclass
class Finding:
    rule: str
    file: str
    line: int
    snippet: str
    detail: str = ""
    severity: str = ""
    title: str = ""
    why: str = ""
    fix: str = ""

    def __post_init__(self) -> None:
        meta = RULES[self.rule]
        self.severity = self.severity or SEVERITY[self.rule]
        self.title = meta["title"]
        self.why = meta["why"]
        self.fix = meta["fix"]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanResult:
    root: str
    files_scanned: int = 0
    uses_experiments: bool = False
    findings: list[Finding] = field(default_factory=list)
    flag_keys: set[str] = field(default_factory=set)
    event_names: set[str] = field(default_factory=set)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "block"]

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "files_scanned": self.files_scanned,
            "uses_experiments": self.uses_experiments,
            "flag_keys": sorted(self.flag_keys),
            "event_names": sorted(self.event_names),
            "blocking": len(self.blocking),
            "warnings": len(self.findings) - len(self.blocking),
            "findings": [f.to_dict() for f in sorted(self.findings, key=lambda f: (f.rule, f.file, f.line))],
        }


# ---------------------------------------------------------------- python


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _identifier_arg(call: ast.Call) -> ast.expr | None:
    node = _kw(call, "identifier")
    if node is None and len(call.args) >= 2:
        node = call.args[1]
    return node


def _roots(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    funcs = {id(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
    funcs |= {id(c.func.value) for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and id(n) not in funcs}


def _is_random(node: ast.AST | None) -> bool:
    """True when the expression is only a fresh random value (no stored ID first)."""
    if node is None:
        return False
    if isinstance(node, ast.BoolOp) or isinstance(node, ast.IfExp):
        return False  # "cookie or uuid4()" keeps a stored ID when there is one
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    if not calls:
        return False
    text = ast.unparse(node).lower()
    if any(h in text for h in PERSIST_HINTS):
        return False
    return any(_call_name(c) in RANDOM_CALLS for c in calls)


class _PyScanner(ast.NodeVisitor):
    def __init__(self, path: str, source: str, result: ScanResult):
        self.path = path
        self.lines = source.splitlines()
        self.result = result
        self.clients: list[ast.Call] = []
        self.exposures: list[tuple[ast.Call, ast.AST | None]] = []
        self.conversions: list[tuple[ast.Call, ast.AST | None]] = []
        self.assignments: dict[int, dict[str, ast.expr]] = {}
        self.func_stack: list[ast.AST] = []
        self.has_close = False
        self.has_main = False
        self.is_server = False
        self.buffer_one = False

    def snippet(self, node: ast.AST) -> str:
        ln = getattr(node, "lineno", 1)
        return self.lines[ln - 1].strip() if 0 < ln <= len(self.lines) else ""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.func_stack.append(node)
        scope: dict[str, ast.expr] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                scope[sub.targets[0].id] = sub.value
        self.assignments[id(node)] = scope
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        t = ast.unparse(node.test)
        if "__name__" in t and "__main__" in t:
            self.has_main = True
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for a in node.names:
            if a.name.split(".")[0] in {"flask", "django", "fastapi", "starlette", "aiohttp", "tornado"}:
                self.is_server = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module and node.module.split(".")[0] in {"flask", "django", "fastapi", "starlette", "aiohttp", "tornado"}:
            self.is_server = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node)
        if name == "Flagsmith":
            self.clients.append(node)
        elif name == "get_experiment_flag":
            self.result.uses_experiments = True
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                self.result.flag_keys.add(node.args[0].value)
            self.exposures.append((node, self.func_stack[-1] if self.func_stack else None))
        elif name == "track_event":
            self.result.uses_experiments = True
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                self.result.event_names.add(node.args[0].value)
            self.conversions.append((node, self.func_stack[-1] if self.func_stack else None))
        elif name in {"close", "flush", "flush_events"}:
            self.has_close = True
        elif name == "EventProcessorConfig":
            mb = _kw(node, "max_buffer_items")
            if isinstance(mb, ast.Constant) and mb.value == 1:
                self.buffer_one = True
        self.generic_visit(node)

    def resolve(self, expr: ast.AST | None, func: ast.AST | None) -> ast.AST | None:
        if isinstance(expr, ast.Name) and func is not None:
            return self.assignments.get(id(func), {}).get(expr.id, expr)
        return expr

    def finish(self) -> None:
        r = self.result
        uses = bool(self.exposures or self.conversions)
        for c in self.clients:
            if not uses:
                continue
            ev = _kw(c, "enable_events")
            if not (isinstance(ev, ast.Constant) and ev.value is True):
                r.findings.append(Finding("G1", self.path, c.lineno, self.snippet(c)))
            if _kw(c, "default_flag_handler") is None:
                r.findings.append(Finding("G2", self.path, c.lineno, self.snippet(c)))
            api = _kw(c, "api_url")
            if api is not None and "flagsmith.com" not in ast.unparse(api):
                epc = _kw(c, "event_processor_config")
                has_events_url = epc is not None and isinstance(epc, ast.Call) and _kw(epc, "events_api_url") is not None
                if not has_events_url:
                    r.findings.append(Finding("G6", self.path, c.lineno, self.snippet(c),
                                              detail=f"api_url={ast.unparse(api)}"))
        exposure_roots: list[set[str]] = []
        for call, func in self.exposures:
            ident = _identifier_arg(call)
            resolved = self.resolve(ident, func)
            if _is_random(resolved) or _is_random(ident):
                r.findings.append(Finding("G3", self.path, call.lineno, self.snippet(call),
                                          detail=f"identifier comes from {ast.unparse(resolved) if resolved else '?'}"))
            exposure_roots.append(_roots(resolved) | _roots(ident))
        for call, func in self.conversions:
            ident = _identifier_arg(call)
            resolved = self.resolve(ident, func)
            roots = _roots(resolved) | _roots(ident)
            if exposure_roots and roots and all(not (roots & e) for e in exposure_roots):
                exp_txt = ", ".join(sorted(set().union(*exposure_roots))) or "?"
                r.findings.append(Finding("G4", self.path, call.lineno, self.snippet(call),
                                          detail=f"conversion uses {ast.unparse(ident) if ident else '?'}; exposure uses {exp_txt}"))
        if uses and self.has_main and not self.is_server and not (self.has_close or self.buffer_one):
            node = (self.conversions or self.exposures)[-1][0]
            r.findings.append(Finding("G5", self.path, node.lineno, self.snippet(node)))


def scan_python(path: Path, rel: str, result: ScanResult) -> None:
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    s = _PyScanner(rel, source, result)
    s.visit(tree)
    s.finish()


# ---------------------------------------------------------------- javascript

_JS_INIT = re.compile(r"(?:flagsmith|\w+)\s*\.\s*init\s*\(\s*\{(?P<body>.*?)\}\s*\)", re.S)
_JS_NODE = re.compile(r"new\s+Flagsmith\s*\(\s*\{(?P<body>.*?)\}\s*\)", re.S)
_JS_PROVIDER = re.compile(r"<FlagsmithProvider[^>]*options=\{\{(?P<body>.*?)\}\}", re.S)
_JS_EXPO = re.compile(r"(?:getExperimentFlag|useExperiment)\s*\(\s*(['\"`])(?P<flag>[^'\"`]+)\1\s*(?:,\s*(?P<ident>[^)]+?))?\)")
_JS_TRACK = re.compile(r"trackEvent\s*\(\s*(['\"`])(?P<event>[^'\"`]+)\1(?P<rest>[^)]*)\)")
_JS_RANDOM = re.compile(r"(crypto\.randomUUID\(\)|Math\.random\(\)|uuidv4\(\)|uuid\(\)|nanoid\(\)|Date\.now\(\))")


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def scan_js(path: Path, rel: str, result: ScanResult) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    def snip(ln: int) -> str:
        return lines[ln - 1].strip() if 0 < ln <= len(lines) else ""

    expos = list(_JS_EXPO.finditer(text))
    tracks = list(_JS_TRACK.finditer(text))
    if not (expos or tracks):
        return
    result.uses_experiments = True
    for m in expos:
        result.flag_keys.add(m.group("flag"))
    for m in tracks:
        result.event_names.add(m.group("event"))

    inits = [(m, "browser") for m in _JS_INIT.finditer(text)] + [(m, "node") for m in _JS_NODE.finditer(text)] + \
            [(m, "react") for m in _JS_PROVIDER.finditer(text)]
    for m, kind in inits:
        body = m.group("body")
        ln = _line_of(text, m.start())
        if not re.search(r"enableEvents\s*:\s*true", body):
            result.findings.append(Finding("G1", rel, ln, snip(ln)))
        if "defaultFlags" not in body:
            result.findings.append(Finding("G2", rel, ln, snip(ln)))
        api = re.search(r"\b(?:api|apiUrl)\s*:\s*['\"`](?P<u>[^'\"`]+)", body)
        if api and "flagsmith.com" not in api.group("u") and "eventsApiUrl" not in body:
            result.findings.append(Finding("G6", rel, ln, snip(ln), detail=f"api={api.group('u')}"))

    # G3: identity built from a fresh random value with no stored value first
    for m in re.finditer(r"identify\s*\((?P<arg>(?:[^()]|\([^()]*\))*)\)", text):
        arg = m.group("arg").strip()
        ln = _line_of(text, m.start())
        if _JS_RANDOM.search(arg) or _assigned_random(text, arg, m.start()):
            result.findings.append(Finding("G3", rel, ln, snip(ln), detail=f"identify({arg})"))
    expo_idents = []
    for m in expos:
        ident = (m.group("ident") or "").strip()
        ln = _line_of(text, m.start())
        if ident:
            expo_idents.append(ident)
            if _JS_RANDOM.search(ident) or _assigned_random(text, ident, m.start()):
                result.findings.append(Finding("G3", rel, ln, snip(ln), detail=f"identifier {ident}"))
    # G4 (node SDK passes the identifier explicitly)
    for m in tracks:
        rest = m.group("rest")
        ident = re.search(r"identifier\s*:\s*(?P<i>[\w.\[\]'\"]+)", rest)
        if ident and expo_idents and all(ident.group("i") != e for e in expo_idents):
            ln = _line_of(text, m.start())
            result.findings.append(Finding("G4", rel, ln, snip(ln),
                                           detail=f"conversion uses {ident.group('i')}; exposure uses {', '.join(expo_idents)}"))
    # G9: browser getExperimentFlag before any identify()
    first_identify = re.search(r"identify\s*\(", text)
    has_identity_opt = re.search(r"identity\s*:", text)
    for m in expos:
        if m.group("ident") or "useExperiment" in m.group(0):
            continue
        if has_identity_opt:
            continue
        if first_identify is None or first_identify.start() > m.start():
            ln = _line_of(text, m.start())
            result.findings.append(Finding("G9", rel, ln, snip(ln)))
    # G5: node / script exits without flushing
    is_script = bool(re.search(r"process\.exit|^#!.*node", text, re.M)) or path.suffix in {".mjs", ".cjs"}
    if inits and is_script and not re.search(r"flushEvents\s*\(|\.close\s*\(", text):
        ln = _line_of(text, (tracks or expos)[-1].start())
        result.findings.append(Finding("G5", rel, ln, snip(ln)))


def _assigned_random(text: str, name: str, before: int) -> bool:
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return False
    pat = re.compile(rf"(?:const|let|var)\s+{re.escape(name)}\s*=\s*(?P<v>[^;\n]+)")
    found = None
    for m in pat.finditer(text, 0, before):
        found = m
    if not found:
        return False
    v = found.group("v")
    if re.search(r"localStorage|cookie|session|user|\|\||\?\?", v, re.I):
        return False
    return bool(_JS_RANDOM.search(v))


# ---------------------------------------------------------------- entry points


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in PY_EXT | JS_EXT:
            yield p


def scan(root: str | Path) -> ScanResult:
    root = Path(root)
    result = ScanResult(root=str(root))
    for p in iter_files(root):
        rel = p.name if root.is_file() else p.relative_to(root).as_posix()
        result.files_scanned += 1
        if p.suffix in PY_EXT:
            scan_python(p, rel, result)
        else:
            scan_js(p, rel, result)
    return result


def live_checks(result: ScanResult, feature: dict | None, metrics: list[dict]) -> list[Finding]:
    """Checks against the Flagsmith project: G7 (variation keys) and G8 (event names).

    ``feature`` is the experiment feature as returned by the API (with
    ``multivariate_options``); ``metrics`` are the metric objects attached to
    the experiment.
    """
    out: list[Finding] = []
    if feature:
        for opt in feature.get("multivariate_options", []) or []:
            if not (opt.get("key") or "").strip():
                value = opt.get("string_value") or opt.get("value") or opt.get("id")
                out.append(Finding("G7", f"flagsmith:{feature.get('name')}", 0,
                                   f"variation {value!r} has no key", detail=f"option id {opt.get('id')}"))
    code_events = result.event_names
    lower = {e.lower(): e for e in code_events}
    for m in metrics:
        ev = ((m.get("definition") or {}).get("event") or "").strip()
        if not ev or ev in code_events:
            continue
        hint = f"; the code sends {lower[ev.lower()]!r} (different case)" if ev.lower() in lower else \
            (f"; the code sends {', '.join(sorted(code_events))}" if code_events else "; no track_event call found")
        out.append(Finding("G8", f"flagsmith:metric {m.get('name')}", 0,
                           f"metric listens for {ev!r}", detail=f"metric listens for {ev!r}{hint}"))
    return out
