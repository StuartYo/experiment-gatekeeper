"""Nightly job that replays queued checkouts as conversion events."""
import os

from flagsmith import EventProcessorConfig, Flagsmith
from flagsmith.models import DefaultFlag

flagsmith = Flagsmith(
    environment_key=os.environ["FLAGSMITH_ENVIRONMENT_KEY"],
    enable_events=True,
    default_flag_handler=lambda name: DefaultFlag(enabled=True, value="control"),

)


def queued():
    return [("visitor-1", 1), ("visitor-2", 0)]


if __name__ == "__main__":
    for visitor, bought in queued():
        flagsmith.get_experiment_flag("pricing_page", identifier=visitor)
        if bought:
            flagsmith.track_event("checkout", identifier=visitor)

