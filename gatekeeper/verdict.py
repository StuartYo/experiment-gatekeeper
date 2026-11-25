"""Decide what a result is allowed to say.

Input: the payload of Flagsmith's ``GET experiments/<id>/results/`` plus the
pre-registered plan stored at approval time. Output: one of

* ``ship``          – a variant won under the pre-registered rule
* ``drop``          – the variant lost under the same rule; keep control
* ``keep_running``  – not enough data yet to say anything
* ``invalid``       – the data cannot be trusted; fix and restart

with every check listed, so the reason can be shown to a person.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from statistics import NormalDist

from .plan import LOSE_THRESHOLD, SRM_P_THRESHOLD, WIN_THRESHOLD

Z95 = NormalDist().inv_cdf(0.975)


@dataclass
class Check:
    id: str
    label: str
    status: str  # pass | fail | wait | warn
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    decision: str
    headline: str
    winner: str | None = None
    checks: list[Check] = field(default_factory=list)
    arms: list[dict] = field(default_factory=list)
    flagsmith_says: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        return d


def chi2_sf(stat: float, df: int) -> float:
    """Survival function of the chi-squared distribution (regularised gamma)."""
    if stat <= 0:
        return 1.0
    a, x = df / 2.0, stat / 2.0
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(500):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    # continued fraction for the upper tail
    b = x + 1 - a
    c = 1e300
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h)


def srm_p(observed: list[int], shares: list[float]) -> float | None:
    total = sum(observed)
    if len(observed) < 2 or total == 0 or any(s <= 0 for s in shares):
        return None
    stat = sum((o - total * s) ** 2 / (total * s) for o, s in zip(observed, shares))
    return chi2_sf(stat, len(observed) - 1)


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%" if x is not None else "n/a"


def decide(payload: dict | None, plan: dict, *, approved_hash: str | None = None, current_hash: str | None = None,
           peeks_before_target: int = 0, days_running: float | None = None,
           primary_metric_id: int | None = None) -> Verdict:
    """``plan`` is the stored pre-registration: ``decision_rule`` and ``split``
    (variant key -> share, control included)."""
    rule = plan["decision_rule"]
    split: dict[str, float] = plan["split"]
    checks: list[Check] = []

    # 1. the experiment still matches what was approved
    if approved_hash and current_hash and approved_hash != current_hash:
        checks.append(Check("V1", "Setup unchanged since approval", "fail",
                            "The flag, split or metrics changed after approval, so earlier and later visitors were measured differently."))
    else:
        checks.append(Check("V1", "Setup unchanged since approval", "pass",
                            "Flag, split and metrics match the approved plan."))

    if not payload or not payload.get("metrics"):
        checks.append(Check("V2", "Results computed", "wait", "Flagsmith has not computed results yet."))
        return _finish("keep_running", "No results yet.", None, checks, [], "")

    metrics = payload["metrics"]
    metric = next((m for m in metrics if m.get("metric_id") == primary_metric_id), metrics[0])
    variants: dict[str, dict] = metric.get("variants") or {}
    inference: dict[str, dict | None] = metric.get("inference") or {}

    keys = [k for k in split if k in variants] or list(variants)
    arms = []
    for k in keys:
        v = variants.get(k) or {"n": 0, "sum": 0}
        inf = inference.get(k)
        arms.append({"variant": k, "exposures": int(v.get("n", 0)), "conversions": v.get("sum", 0),
                     "rate": (v.get("sum", 0) / v["n"]) if v.get("n") else None,
                     "expected_share": split.get(k),
                     "lift": inf.get("lift") if inf else None, "ci_low": inf.get("ci_low") if inf else None,
                     "ci_high": inf.get("ci_high") if inf else None,
                     "chance_to_win": inf.get("chance_to_win") if inf else None})

    # 2. sample ratio mismatch against the approved split (not the environment default)
    observed = [a["exposures"] for a in arms]
    shares = [split.get(a["variant"], 0) for a in arms]
    norm = sum(shares) or 1
    p = srm_p(observed, [s / norm for s in shares])
    fs_p = payload.get("srm_p_value")
    fs_note = f" Flagsmith's own SRM check: {'not run' if fs_p is None else f'p={fs_p:.3g}'}."
    if p is None:
        checks.append(Check("V2", "Traffic split matches the plan", "wait", "Not enough exposures to test the split." + fs_note))
    elif p < SRM_P_THRESHOLD:
        exp_txt = " / ".join(f"{s / norm:.0%}" for s in shares)
        obs_txt = " / ".join(f"{o / max(1, sum(observed)):.1%}" for o in observed)
        checks.append(Check("V2", "Traffic split matches the plan", "fail",
                            f"Planned {exp_txt}, observed {obs_txt} (chi-squared p={p:.2g}). Something other than chance decides who lands in which group: bots, a redirect, a crash in one variant, or a broken visitor ID." + fs_note))
    else:
        checks.append(Check("V2", "Traffic split matches the plan", "pass", f"Chi-squared p={p:.2f} against the approved split." + fs_note))

    # 3. enough visitors per arm (pre-registered)
    need = int(rule["min_exposures_per_arm"])
    short = [a for a in arms if a["exposures"] < need]
    if short:
        worst = min(a["exposures"] for a in arms)
        checks.append(Check("V3", "Planned sample size reached", "wait",
                            f"{worst:,} of {need:,} visitors in the smallest group ({worst / need:.0%}). Any winner before this is mostly noise."))
    else:
        checks.append(Check("V3", "Planned sample size reached", "pass", f"Every group has at least {need:,} visitors."))

    if rule.get("min_days") and days_running is not None:
        if days_running < rule["min_days"]:
            checks.append(Check("V4", "Planned run time reached", "wait",
                                f"Running {days_running:.1f} of {rule['min_days']} planned days (covers weekday and weekend behaviour)."))
        else:
            checks.append(Check("V4", "Planned run time reached", "pass", f"Ran {days_running:.1f} days."))

    # 5. peeking
    if peeks_before_target > 3 and short:
        checks.append(Check("V5", "Results not read too early", "warn",
                            f"Results were read {peeks_before_target} times before the sample size was reached. Stopping on the first good-looking read inflates false winners."))

    # 6. the decision itself
    treat = [a for a in arms if a["chance_to_win"] is not None]
    best = max(treat, key=lambda a: a["chance_to_win"]) if treat else None
    flagsmith_says = ""
    if best:
        flagsmith_says = (f"Flagsmith: {best['variant']} has a {best['chance_to_win']:.0%} chance to win, "
                          f"lift {_fmt_pct(best['lift'])} (95% interval {_fmt_pct(best['ci_low'])} to {_fmt_pct(best['ci_high'])}).")
        if best["chance_to_win"] >= WIN_THRESHOLD:
            checks.append(Check("V6", f"Chance to win at least {WIN_THRESHOLD:.0%}", "pass",
                                f"{best['variant']}: {best['chance_to_win']:.1%}."))
        elif all(a["chance_to_win"] <= LOSE_THRESHOLD for a in treat):
            checks.append(Check("V6", f"Chance to win at least {WIN_THRESHOLD:.0%}", "fail",
                                f"Every variant is at or below {LOSE_THRESHOLD:.0%}: control is better."))
        else:
            checks.append(Check("V6", f"Chance to win at least {WIN_THRESHOLD:.0%}", "wait",
                                f"Best is {best['variant']} at {best['chance_to_win']:.1%}, below the {WIN_THRESHOLD:.0%} bar set before the test."))
    else:
        checks.append(Check("V6", f"Chance to win at least {WIN_THRESHOLD:.0%}", "wait", "No inference yet."))

    if any(c.status == "fail" and c.id in {"V1", "V2"} for c in checks):
        return _finish("invalid", "Data invalid: do not use these numbers.", None, checks, arms, flagsmith_says)
    if any(c.status == "wait" for c in checks if c.id in {"V3", "V4"}):
        return _finish("keep_running", "Keep running: the planned sample is not reached.", None, checks, arms, flagsmith_says)
    v6 = next(c for c in checks if c.id == "V6")
    if v6.status == "pass" and best:
        return _finish("ship", f"Ship {best['variant']}.", best["variant"], checks, arms, flagsmith_says)
    if v6.status == "fail":
        return _finish("drop", "Keep control: the variant lost.", "control", checks, arms, flagsmith_says)
    return _finish("keep_running", "No clear winner yet at the planned bar.", None, checks, arms, flagsmith_says)


def _finish(decision, headline, winner, checks, arms, fs) -> Verdict:
    return Verdict(decision=decision, headline=headline, winner=winner, checks=checks, arms=arms, flagsmith_says=fs)
