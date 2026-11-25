"""Demo signup page with three planted experiment bugs (see README, "Demo app").

This is what a coding agent typically produces in a hurry. Gatekeeper's
preflight finds the bugs; do not copy this file.
"""
import os
import uuid

from flagsmith import Flagsmith
from flask import Flask, render_template_string, request

app = Flask(__name__)
flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
    api_url=os.environ.get("FLAGSMITH_SDK_URL", "http://localhost:8000/api/v1/"),
)

FORMS = {
    "control": ["email", "password", "name", "company", "role", "phone"],
    "short": ["email", "password", "name"],
}
PAGE = """<form method=post action=/signup>{% for f in fields %}<input name={{f}} placeholder={{f}}>{% endfor %}<button>Create account</button></form>"""


@app.get("/")
def signup_page():
    visitor_id = str(uuid.uuid4())
    flag = flagsmith.get_experiment_flag("signup_form", identifier=visitor_id)
    fields = FORMS.get(str(flag.value), FORMS["control"])
    return render_template_string(PAGE, fields=fields)


@app.post("/signup")
def signup():
    email = request.form["email"]
    flagsmith.track_event("signup_completed", identifier=email)
    return "Welcome!"
