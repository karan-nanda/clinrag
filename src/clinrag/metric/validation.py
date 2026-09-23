"""Phase 5: validate the automatic metric against human annotation.

Without this the paper does not stand up. An automatic faithfulness metric is a measuring
instrument, and an uncalibrated instrument produces numbers, not findings. The protocol
requires 150-300 claims, two independent annotators, and reported agreement.

What gets measured, and why each is separate:

* **Inter-annotator agreement** (Cohen's kappa) -- can humans even agree on this task? If
  kappa is low, the construct is underspecified and no judge can be validated against it.
  This is a result about the task, and it is reported whatever it says.
* **Automatic-vs-human agreement, per label** -- the judge may be fine on `supported` and
  useless on `contradicted`, which are very different failures. A single accuracy number
  hides that, so per-label precision and recall are reported.
* **`unsupported` handled separately throughout.** It is the label most likely to be
  confused with "false" by an annotator, so the annotation sheet states the distinction and
  the analysis never merges the two.

Sampling is stratified across arm and model, and deliberately **over-samples cases where the
judge and the generator share a model family**, because that is where self-preference bias
would show up.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path

LABELS = ("supported", "unsupported", "contradicted")

ANNOTATION_GUIDE = """\
# Claim verification - annotation guide

For each row, read the CLAIM and the EVIDENCE passages, then choose ONE label.

  supported     The passages state or directly entail the claim.
  unsupported   The passages neither state nor contradict it.
  contradicted  A passage asserts something incompatible with the claim.

THE CRITICAL RULE: judge ONLY what the passages say.

A claim you know to be true, but which the passages do not state, is `unsupported` - NOT
`supported`. `unsupported` does not mean the claim is false. It records that this evidence
does not settle it. Most disagreement in pilot annotation comes from annotators importing
their own genetics knowledge; resist that.

If a claim is too vague to check at all, label it `unsupported` and note "vague" in the
notes column.

Do not discuss rows with the other annotator before both of you have finished.
"""


@dataclass
class AgreementReport:
    n: int
    raw_agreement: float
    kappa: float
    per_label: dict
    confusion: dict

    def summary(self) -> str:
        return (
            f"n={self.n}  raw agreement={self.raw_agreement:.3f}  "
            f"Cohen's kappa={self.kappa:.3f} ({interpret_kappa(self.kappa)})"
        )


def interpret_kappa(k: float) -> str:
    """Landis & Koch bands. Reported as a descriptor, never as a pass/fail gate."""
    if k < 0.0:
        return "worse than chance"
    for bound, name in ((0.20, "slight"), (0.40, "fair"), (0.60, "moderate"),
                        (0.80, "substantial")):
        if k <= bound:
            return name
    return "almost perfect"


def cohens_kappa(a: list[str], b: list[str], labels: tuple[str, ...] = LABELS) -> float:
    """Chance-corrected agreement between two raters.

    Returns 1.0 when both raters are constant and identical -- with no observed variance,
    kappa is undefined, and reporting 1.0 with the raw agreement beside it is less
    misleading than a NaN that gets dropped from a table.
    """
    if len(a) != len(b):
        raise ValueError(f"rater lengths differ: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        raise ValueError("no annotations to compare")

    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    expected = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def confusion(a: list[str], b: list[str], labels: tuple[str, ...] = LABELS) -> dict:
    m = {x: {y: 0 for y in labels} for x in labels}
    for x, y in zip(a, b):
        if x in m and y in m[x]:
            m[x][y] += 1
    return m


def per_label_scores(gold: list[str], pred: list[str], labels=LABELS) -> dict:
    """Precision, recall and F1 of `pred` against `gold`, per label.

    A judge can be strong on `supported` and poor on `contradicted`; a single accuracy figure
    would hide exactly the failure that matters most.
    """
    out = {}
    for label in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred) if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall else None
        )
        out[label] = {
            "support": sum(1 for g in gold if g == label),
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }
    return out


def agreement(a: list[str], b: list[str]) -> AgreementReport:
    n = len(a)
    return AgreementReport(
        n=n,
        raw_agreement=sum(1 for x, y in zip(a, b) if x == y) / n if n else 0.0,
        kappa=cohens_kappa(a, b),
        per_label=per_label_scores(a, b),
        confusion=confusion(a, b),
    )


# ------------------------------------------------------------------ sampling


def sample_for_annotation(
    claims: list,
    verdicts: list,
    n: int = 200,
    seed: int = 0,
    oversample_same_family: float = 0.3,
) -> list:
    """Stratified sample of claims for human annotation.

    Strata are (arm, automatic label), so rare labels -- `contradicted` above all -- are not
    swamped by the common ones. A sample drawn proportionally would contain almost no
    contradictions and could not measure the judge where it matters most.

    `oversample_same_family` reserves a share of the sample for claims whose judge shares a
    model family with the generator, to measure self-preference bias directly.
    """
    by_id = {v.claim_id: v for v in verdicts}
    rng = random.Random(seed)

    pool = [c for c in claims if c.claim_id in by_id]
    if not pool:
        return []

    same_family = [c for c in pool if by_id[c.claim_id].same_family_as_generator]
    quota_sf = min(int(n * oversample_same_family), len(same_family))
    chosen = rng.sample(same_family, quota_sf) if quota_sf else []
    chosen_ids = {c.claim_id for c in chosen}

    strata: dict[tuple[str, str], list] = {}
    for c in pool:
        if c.claim_id in chosen_ids:
            continue
        strata.setdefault((c.arm, by_id[c.claim_id].label), []).append(c)

    remaining = n - len(chosen)
    if remaining > 0 and strata:
        per = max(1, remaining // len(strata))
        for key in sorted(strata):
            bucket = strata[key]
            take = min(per, len(bucket))
            chosen.extend(rng.sample(bucket, take))

    rng.shuffle(chosen)
    return chosen[:n]


# ------------------------------------------------------------------- sheets


def write_annotation_sheet(
    path: Path,
    claims_sample: list,
    pools: dict[str, list[dict]],
    include_auto_label: bool = False,
    verdicts: list | None = None,
) -> Path:
    """Write a CSV one annotator can fill in.

    `include_auto_label` defaults to False and should stay False for the validation sample:
    showing the annotator the judge's answer anchors them to it, and the resulting agreement
    figure would be inflated and meaningless.
    """
    by_id = {v.claim_id: v for v in (verdicts or [])}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        header = ["claim_id", "variation_id", "claim_text", "evidence", "label", "notes"]
        if include_auto_label:
            header.insert(-2, "auto_label")
        w.writerow(header)

        for c in claims_sample:
            pool = pools.get(c.variation_id, [])
            evidence = "\n\n".join(
                f"[{i}] {p.get('text', '').strip()}" for i, p in enumerate(pool)
            ) or "(no evidence provided)"
            row = [c.claim_id, c.variation_id, c.text, evidence, "", ""]
            if include_auto_label:
                row.insert(-2, by_id[c.claim_id].label if c.claim_id in by_id else "")
            w.writerow(row)

    (path.parent / "ANNOTATION_GUIDE.md").write_text(ANNOTATION_GUIDE, encoding="utf-8")
    return path


def read_annotations(path: Path) -> dict[str, str]:
    """Read a completed sheet as claim_id -> label, skipping unfilled and invalid rows."""
    out: dict[str, str] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("label") or "").strip().lower()
            if label in LABELS:
                out[row["claim_id"]] = label
    return out


def compare(
    annotator_a: dict[str, str],
    annotator_b: dict[str, str],
    automatic: dict[str, str] | None = None,
) -> dict:
    """Inter-annotator agreement, and optionally the automatic metric against both.

    The automatic metric is scored against each annotator separately and against the subset
    where they agree. Scoring only against the agreed subset would flatter the judge by
    removing every genuinely hard case.
    """
    shared = sorted(set(annotator_a) & set(annotator_b))
    if not shared:
        raise ValueError("annotators share no claim ids")

    a = [annotator_a[i] for i in shared]
    b = [annotator_b[i] for i in shared]
    result = {"inter_annotator": agreement(a, b), "n_shared": len(shared)}

    if automatic:
        both = [i for i in shared if annotator_a[i] == annotator_b[i]]
        result["auto_vs_a"] = agreement(a, [automatic.get(i, "unsupported") for i in shared])
        result["auto_vs_b"] = agreement(b, [automatic.get(i, "unsupported") for i in shared])
        if both:
            result["auto_vs_consensus"] = agreement(
                [annotator_a[i] for i in both],
                [automatic.get(i, "unsupported") for i in both],
            )
            result["n_consensus"] = len(both)
        result["n_disagreed"] = len(shared) - len(both)

    return result
