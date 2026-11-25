"""Demo signup page, instrumented correctly for a Flagsmith experiment."""
import os
import uuid

from flagsmith import EventProcessorConfig, Flagsmith
from flagsmith.models import DefaultFlag
from flask import Flask, make_response, render_template_string, request

app = Flask(__name__)
SDK_URL = os.environ.get("FLAGSMITH_SDK_URL", "http://localhost:8000/api/v1/")
flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
    api_url=SDK_URL,
    enable_events=True,
    event_processor_config=EventProcessorConfig(events_api_url=SDK_URL.replace("/v1/", "/")),
    default_flag_handler=lambda name: DefaultFlag(enabled=True, value="control"),
)

FORMS = {
    "control": ["email", "password", "name", "company", "role", "phone"],
    "short": ["email", "password", "name"],
}
PAGE = """<form method=post action=/signup>{% for f in fields %}<input name={{f}} placeholder={{f}}>{% endfor %}<button>Create account</button></form>"""


def visitor_id() -> str:
    return request.cookies.get("vid") or str(uuid.uuid4())


@app.get("/")
def signup_page():
    vid = visitor_id()
    flag = flagsmith.get_experiment_flag("signup_form", identifier=vid)
    fields = FORMS.get(str(flag.value), FORMS["control"])
    resp = make_response(render_template_string(PAGE, fields=fields))
    resp.set_cookie("vid", vid, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


@app.post("/signup")
def signup():
    flagsmith.track_event("signup_completed", identifier=visitor_id())
    return "Welcome!"
