import os
import uuid

from flagsmith import EventProcessorConfig, Flagsmith
from flagsmith.models import DefaultFlag
from flask import Flask, request, make_response

app = Flask(__name__)
API = "https://edge.api.flagsmith.com/api/v1/"
flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
    enable_events=True,
)


def current_visitor():
    return request.cookies.get("vid") or str(uuid.uuid4())


@app.get("/pricing")
def pricing():
    vid = current_visitor()
    flag = flagsmith.get_experiment_flag("pricing_page", identifier=vid)
    resp = make_response(f"<h1>{flag.value}</h1><a href=/buy>Buy</a>")
    resp.set_cookie("vid", vid)
    return resp


@app.post("/buy")
def buy():
    user_email = request.form.get("email", "")
    flagsmith.track_event("checkout", identifier=current_visitor())
    return "thanks"
