"""Phase 5/6: turn per-claim verdicts into per-explanation rates and arm comparisons.

Two statistical rules are enforced here rather than left to discipline:

**Resample variants, not claims.** Claims within one explanation are not independent -- they
share a prompt, an evidence pool and a generation. Bootstrapping over claims would treat a
40-claim explanation as 40 observations and produce intervals that are far too tight.
`bootstrap_diff` resamples *variants* and recomputes the whole statistic inside each replicate.

**Pair across arms.** Arms are run on the same variants, so the comparison is paired. A paired
bootstrap removes between-variant variance, which is large here (gene, condition and evidence
volume all differ per variant) and would otherwise swamp the arm effect. Only variants present
in *both* arms are used, and the pairing count is reported, because distractor arms drop
variants for reasons documented in the protocol.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

RATE_FIELDS = ("supported", "unsupported", "contradicted", "conflation")


@dataclass
class ExplanationRates:
    """Per-explanation summary: one generation, one row."""

    variation_id: str
    arm: str
    model: str
    n_claims: int = 0
    supported: float = 0.0
    unsupported: float = 0.0
    contradicted: float = 0.0
    conflation: float = 0.0
    by_type: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {
            "variation_id": self.variation_id, "arm": self.arm, "model": self.model,
            "n_claims": self.n_claims,
        }
        d.update({f: getattr(self, f) for f in RATE_FIELDS})
        d["by_type"] = self.by_type
        return d


def explanation_rates(claims: list, verdicts: list) -> list[ExplanationRates]:
    """Collapse claim-level verdicts into one row per (variant, arm, model).

    An explanation that produced no claims is returned with `n_claims=0` and zero rates
    rather than dropped -- a model that says nothing checkable is a result, and silently
    omitting it would bias the rates of every arm it appears in.
    """
    by_claim = {v.claim_id: v for v in verdicts}
    groups: dict[tuple[str, str, str], list] = {}
    for c in claims:
        groups.setdefault((c.variation_id, c.arm, c.model), []).append(c)

    out: list[ExplanationRates] = []
    for (vid, arm, model), group in groups.items():
        n = len(group)
        counts = dict.fromkeys(RATE_FIELDS, 0)
        by_type: dict[str, dict[str, int]] = {}

        for c in group:
            v = by_claim.get(c.claim_id)
            if v is None:
                continue
            counts[v.label] = counts.get(v.label, 0) + 1
            if v.conflation:
                counts["conflation"] += 1
            slot = by_type.setdefault(c.claim_type, dict.fromkeys(RATE_FIELDS, 0))
            slot[v.label] = slot.get(v.label, 0) + 1
            if v.conflation:
                slot["conflation"] += 1

        out.append(
            ExplanationRates(
                variation_id=vid, arm=arm, model=model, n_claims=n,
                **{f: (counts[f] / n if n else 0.0) for f in RATE_FIELDS},
                by_type=by_type,
            )
        )
    return out


def arm_summary(rates: list[ExplanationRates], model: str | None = None) -> dict:
    """Mean of each rate per arm, plus claim counts. Means are over explanations."""
    out: dict[str, dict] = {}
    for arm in sorted({r.arm for r in rates}):
        rows = [r for r in rates if r.arm == arm and (model is None or r.model == model)]
        if not rows:
            continue
        out[arm] = {
            "n_explanations": len(rows),
            "n_claims": sum(r.n_claims for r in rows),
            "mean_claims": round(sum(r.n_claims for r in rows) / len(rows), 2),
            **{
                f: round(sum(getattr(r, f) for r in rows) / len(rows), 4)
                for f in RATE_FIELDS
            },
        }
    return out


@dataclass
class BootstrapResult:
    arm_a: str
    arm_b: str
    metric: str
    n_pairs: int
    mean_a: float
    mean_b: float
    diff: float
    ci_low: float
    ci_high: float
    n_boot: int

    @property
    def crosses_zero(self) -> bool:
        return self.ci_low <= 0.0 <= self.ci_high

    def summary(self) -> str:
        verdict = "n.s." if self.crosses_zero else "significant"
        return (
            f"{self.metric}: {self.arm_a}={self.mean_a:.3f} vs {self.arm_b}={self.mean_b:.3f}  "
            f"diff={self.diff:+.3f} [{self.ci_low:+.3f}, {self.ci_high:+.3f}] "
            f"({verdict}, n={self.n_pairs} paired variants)"
        )


def bootstrap_diff(
    rates: list[ExplanationRates],
    arm_a: str,
    arm_b: str,
    metric: str = "unsupported",
    model: str | None = None,
    n_boot: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Paired bootstrap CI on (arm_a - arm_b) for one rate, resampling over variants.

    Returns a CI on the *difference*. Reporting two separate per-arm intervals and checking
    whether they overlap is a weaker and different test; do not substitute it.
    """
    if metric not in RATE_FIELDS:
        raise ValueError(f"unknown metric {metric!r}; expected one of {RATE_FIELDS}")

    def index(arm: str) -> dict[str, float]:
        return {
            r.variation_id: getattr(r, metric)
            for r in rates
            if r.arm == arm and (model is None or r.model == model)
        }

    a, b = index(arm_a), index(arm_b)
    shared = sorted(set(a) & set(b))
    if not shared:
        raise ValueError(f"no variants appear in both {arm_a!r} and {arm_b!r}")

    pairs = [(a[v], b[v]) for v in shared]
    mean_a = sum(p[0] for p in pairs) / len(pairs)
    mean_b = sum(p[1] for p in pairs) / len(pairs)

    rng = random.Random(seed)
    n = len(pairs)
    diffs = []
    for _ in range(n_boot):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(sum(x - y for x, y in sample) / n)
    diffs.sort()

    lo = diffs[int((alpha / 2) * n_boot)]
    hi = diffs[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]

    return BootstrapResult(
        arm_a=arm_a, arm_b=arm_b, metric=metric, n_pairs=n,
        mean_a=mean_a, mean_b=mean_b, diff=mean_a - mean_b,
        ci_low=lo, ci_high=hi, n_boot=n_boot,
    )


def stratified_summary(
    rates: list[ExplanationRates],
    strata: dict[tuple[str, str], bool],
    arm: str,
    metric: str = "unsupported",
) -> dict:
    """Split one arm's rates by a per-(variant, arm) boolean, e.g. `meta.names_donor`.

    The protocol requires distractor arms be stratified on whether the evidence names the
    donor variant: where it names none, no conflation signal can exist, and pooling dilutes
    the effect with cases that could never have shown it.
    """
    buckets: dict[str, list[float]] = {"true": [], "false": [], "unknown": []}
    for r in rates:
        if r.arm != arm:
            continue
        flag = strata.get((r.variation_id, r.arm))
        key = "unknown" if flag is None else ("true" if flag else "false")
        buckets[key].append(getattr(r, metric))

    return {
        k: {"n": len(v), "mean": round(sum(v) / len(v), 4) if v else None}
        for k, v in buckets.items()
    }
