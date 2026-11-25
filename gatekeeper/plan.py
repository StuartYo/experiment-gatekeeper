"""Pre-registration: how many visitors an experiment needs, and the rule that
will decide it, written down before any data exists.

The sample size uses the standard two-proportion normal approximation. The
decision rule mirrors what Flagsmith's own results page reports (lift, 95%
credible interval, chance to win) so the verdict can be read straight off the
Flagsmith results.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import NormalDist

Z = NormalDist()

# Frozen thresholds (DESIGN.md "Statistics"): changing these changes every verdict.
SRM_P_THRESHOLD = 0.001
WIN_THRESHOLD = 0.95
LOSE_THRESHOLD = 0.05


@dataclass
class Plan:
    baseline_rate: float  # control conversion rate, 0-1
    mde_relative: float  # smallest relative lift worth detecting, e.g. 0.10 == +10%
    alpha: float = 0.05
    power: float = 0.8
    variants: int = 2
    daily_visitors: int | None = None
    min_days: int = 0

    def per_arm(self) -> int:
        return sample_size_per_arm(self.baseline_rate, self.mde_relative, self.alpha, self.power)

    def total(self) -> int:
        return self.per_arm() * self.variants

    def days_needed(self) -> float | None:
        if not self.daily_visitors:
            return None
        return max(self.min_days, math.ceil(self.total() / self.daily_visitors))

    def decision_rule(self) -> dict:
        return {
            "min_exposures_per_arm": self.per_arm(),
            "min_days": self.min_days,
            "ship_if_chance_to_win_at_least": WIN_THRESHOLD,
            "drop_if_chance_to_win_at_most": LOSE_THRESHOLD,
            "invalid_if_srm_p_below": SRM_P_THRESHOLD,
            "mde_relative": self.mde_relative,
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(per_arm=self.per_arm(), total=self.total(), days_needed=self.days_needed(),
                 decision_rule=self.decision_rule())
        return d


def sample_size_per_arm(p1: float, mde_relative: float, alpha: float = 0.05, power: float = 0.8) -> int:
    if not 0 < p1 < 1:
        raise ValueError("baseline rate must be between 0 and 1 (e.g. 0.30 for 30%)")
    if mde_relative <= 0:
        raise ValueError("minimum detectable effect must be positive (e.g. 0.10 for +10%)")
    p2 = p1 * (1 + mde_relative)
    if p2 >= 1:
        raise ValueError("baseline x (1 + effect) must stay below 100%")
    z_a = Z.inv_cdf(1 - alpha / 2)
    z_b = Z.inv_cdf(power)
    p_bar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * p_bar * (1 - p_bar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p2 - p1) ** 2)
