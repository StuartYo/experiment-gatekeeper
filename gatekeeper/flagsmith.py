"""Thin client for the Flagsmith Admin REST API (Cloud or self-hosted).

Only the calls Gatekeeper needs. Every request goes through ``_req`` so the
console can show the exact API calls that were made.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx


class FlagsmithError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body: str):
        self.status = status
        super().__init__(f"Flagsmith {method} {path} -> {status}: {body[:400]}")


@dataclass
class Settings:
    api_url: str = "https://api.flagsmith.com/api/v1/"
    api_key: str = ""
    auth_scheme: str = "Api-Key"  # organisation API key; self-hosted user tokens use "Token"
    environment_key: str = ""
    project_id: int | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        api_url = os.environ.get("FLAGSMITH_API_URL", cls.api_url)
        if not api_url.endswith("/"):
            api_url += "/"
        pid = os.environ.get("FLAGSMITH_PROJECT_ID")
        return cls(
            api_url=api_url,
            api_key=os.environ.get("FLAGSMITH_API_KEY", ""),
            auth_scheme=os.environ.get("FLAGSMITH_AUTH_SCHEME", "Api-Key"),
            environment_key=os.environ.get("FLAGSMITH_ENVIRONMENT", ""),
            project_id=int(pid) if pid else None,
        )

    @property
    def dashboard_url(self) -> str:
        if "api.flagsmith.com" in self.api_url:
            return "https://app.flagsmith.com"
        return self.api_url.split("/api/")[0]


@dataclass
class Client:
    settings: Settings
    calls: list[dict] = field(default_factory=list)
    _http: httpx.Client | None = None

    def __post_init__(self) -> None:
        if not self.settings.api_key:
            raise FlagsmithError(0, "-", "-", "FLAGSMITH_API_KEY is not set. Create an Admin API key in Organisation settings > API keys.")
        self._http = httpx.Client(
            base_url=self.settings.api_url,
            headers={"Authorization": f"{self.settings.auth_scheme} {self.settings.api_key}",
                     "Accept": "application/json"},
            timeout=30,
        )

    # ------------------------------------------------------------------ core
    def _req(self, method: str, path: str, json: dict | None = None, params: dict | None = None,
             ok: tuple[int, ...] = (200, 201, 202, 204)):
        t0 = time.time()
        r = self._http.request(method, path, json=json, params=params)
        self.calls.append({"method": method, "path": path, "status": r.status_code,
                           "ms": round((time.time() - t0) * 1000)})
        if r.status_code not in ok:
            raise FlagsmithError(r.status_code, method, path, r.text)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    @staticmethod
    def _items(data):
        return data["results"] if isinstance(data, dict) and "results" in data else data

    # ------------------------------------------------------------------ project / environment
    def environment(self, key: str | None = None) -> dict:
        return self._req("GET", f"environments/{key or self.settings.environment_key}/")

    def project_id(self) -> int:
        if self.settings.project_id is None:
            self.settings.project_id = int(self.environment()["project"])
        return self.settings.project_id

    def warehouse_connections(self) -> list[dict]:
        return self._items(self._req("GET", f"environments/{self.settings.environment_key}/warehouse-connections/"))

    # ------------------------------------------------------------------ features
    def find_feature(self, name: str) -> dict | None:
        data = self._req("GET", f"projects/{self.project_id()}/features/", params={"search": name, "page_size": 100})
        for f in self._items(data):
            if f["name"] == name:
                return f
        return None

    def feature(self, feature_id: int) -> dict:
        return self._req("GET", f"projects/{self.project_id()}/features/{feature_id}/")

    def create_multivariate_feature(self, name: str, control_value: str, description: str = "") -> dict:
        return self._req("POST", f"projects/{self.project_id()}/features/", json={
            "name": name, "type": "MULTIVARIATE", "initial_value": control_value,
            "default_enabled": True, "description": description,
        })

    def add_variation(self, feature_id: int, value: str, key: str | None, weight: float = 0) -> dict:
        body = {"type": "unicode", "string_value": value, "default_percentage_allocation": weight,
                "feature": feature_id}
        if key:
            body["key"] = key
        return self._req("POST", f"projects/{self.project_id()}/features/{feature_id}/mv-options/", json=body)

    def variations(self, feature_id: int) -> list[dict]:
        return self._items(self._req("GET", f"projects/{self.project_id()}/features/{feature_id}/mv-options/"))

    # ------------------------------------------------------------------ metrics
    def metrics(self) -> list[dict]:
        return self._items(self._req("GET", f"environments/{self.settings.environment_key}/experiment-metrics/"))

    def create_metric(self, name: str, event: str, direction: str = "up") -> dict:
        return self._req("POST", f"environments/{self.settings.environment_key}/experiment-metrics/", json={
            "name": name, "aggregation": "occurrence", "direction": direction,
            "definition": {"version": 1, "event": event},
        })

    # ------------------------------------------------------------------ experiments
    def _exp(self, suffix: str = "") -> str:
        return f"environments/{self.settings.environment_key}/experiments/{suffix}"

    def experiments(self) -> list[dict]:
        return self._items(self._req("GET", self._exp()))

    def experiment(self, exp_id: int) -> dict:
        return self._req("GET", self._exp(f"{exp_id}/"))

    def experiment_metrics(self, exp_id: int) -> list[dict]:
        return self._items(self._req("GET", self._exp(f"{exp_id}/metrics/")))

    def create_experiment(self, feature_id: int, name: str, hypothesis: str, metric_id: int,
                          split: dict[int, float], control_value: str) -> dict:
        """``split`` maps variation (mv option) id -> percentage for that variation."""
        return self._req("POST", self._exp(), json={
            "feature": feature_id, "name": name, "hypothesis": hypothesis,
            "metrics": [{"metric": metric_id, "expected_direction": "increase"}],
            "experiment_rollout": {
                "enabled": True, "rollout_percentage": 100,
                "feature_state_value": {"type": "string", "value": control_value},
                "multivariate_feature_state_values": [
                    {"multivariate_feature_option": oid, "percentage_allocation": pct} for oid, pct in split.items()
                ],
            },
        })

    def start(self, exp_id: int) -> dict:
        return self._req("POST", self._exp(f"{exp_id}/start/"))

    def complete(self, exp_id: int, decision: str, variant_id: int | None = None) -> dict:
        body: dict = {"rollout_decision": decision}
        if variant_id is not None:
            body["rollout_variant"] = variant_id
        return self._req("POST", self._exp(f"{exp_id}/complete/"), json=body)

    def results(self, exp_id: int) -> dict | None:
        return (self._req("GET", self._exp(f"{exp_id}/results/")) or {}).get("results")

    def refresh_results(self, exp_id: int) -> bool:
        try:
            self._req("POST", self._exp(f"{exp_id}/results/refresh/"))
            return True
        except FlagsmithError as e:
            if e.status in (400, 429):
                return False
            raise
