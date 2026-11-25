from pathlib import Path

from gatekeeper import preflight as pf

DEMO = Path(__file__).parent.parent / "demo"


def rules(path):
    return sorted({f.rule for f in pf.scan(path).findings})


def test_broken_demo_is_blocked():
    r = pf.scan(DEMO / "signup_broken")
    assert {"G1", "G3", "G4", "G6"} <= {f.rule for f in r.blocking}
    assert r.flag_keys == {"signup_form"} and r.event_names == {"signup_completed"}


def test_fixed_demo_is_clean():
    assert rules(DEMO / "signup_fixed") == []


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_cookie_or_uuid_is_not_g3(tmp_path):
    p = write(tmp_path, "a.py", """
from flagsmith import Flagsmith
fs = Flagsmith(environment_key="k", enable_events=True, default_flag_handler=lambda n: None)
def view(request):
    vid = request.cookies.get("vid") or str(uuid4())
    fs.get_experiment_flag("f", identifier=vid)
    fs.track_event("e", identifier=vid)
""")
    assert rules(p) == []


def test_script_without_flush_is_g5(tmp_path):
    p = write(tmp_path, "job.py", """
from flagsmith import Flagsmith
fs = Flagsmith(environment_key="k", enable_events=True, default_flag_handler=lambda n: None)
if __name__ == "__main__":
    for i in range(10):
        fs.get_experiment_flag("f", identifier=f"u{i}")
        fs.track_event("e", identifier=f"u{i}")
""")
    assert rules(p) == ["G5"]


def test_js_browser_rules(tmp_path):
    p = write(tmp_path, "page.js", """
import flagsmith from '@flagsmith/flagsmith';
await flagsmith.init({ environmentID: 'x', api: 'https://flags.example.com/api/v1/' });
const flag = flagsmith.getExperimentFlag('hero');
await flagsmith.identify(crypto.randomUUID());
flagsmith.trackEvent('signup');
""")
    assert rules(p) == ["G1", "G2", "G3", "G6", "G9"]


def test_js_clean(tmp_path):
    p = write(tmp_path, "page.js", """
import flagsmith from '@flagsmith/flagsmith';
await flagsmith.init({ environmentID: 'x', enableEvents: true, defaultFlags: { hero: { enabled: true, value: 'control' } } });
const id = localStorage.getItem('vid') || crypto.randomUUID();
await flagsmith.identify(id);
const flag = flagsmith.getExperimentFlag('hero');
flagsmith.trackEvent('signup');
""")
    assert rules(p) == []


def test_live_checks():
    r = pf.scan(DEMO / "signup_fixed")
    feature = {"name": "signup_form", "multivariate_options": [{"id": 1, "key": "", "string_value": "short"}]}
    out = pf.live_checks(r, feature, [{"name": "signups", "definition": {"event": "Signup_Completed"}}])
    assert [f.rule for f in out] == ["G7", "G8"]
    assert "different case" in out[1].detail
