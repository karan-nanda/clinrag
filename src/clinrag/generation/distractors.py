"""Phase 4: distractor assignment for the two distractor arms.

Both distractors are **label-matched** to their target. An opposite-label distractor would
differ from real evidence in two ways at once -- relevance *and* direction -- confounding
"the model ignores irrelevant evidence" with "the model is swayed by contrary evidence".
Matching labels leaves variant identity as the only thing wrong with the evidence, which is
what the gene/variant conflation category needs.

`distractor_same_gene` is the sharp arm: the evidence is about the right gene, the right
condition, and the wrong variant. `distractor_other_gene` is the sanity check.
"""

from __future__ import annotations

import random
import re


def assign(
    variants: list[dict],
    seed: int = 0,
) -> dict[str, dict[str, str]]:
    """Map variation_id -> {"same_gene": vid, "other_gene": vid}.

    Assignment is deterministic given `seed`. Distractors are drawn from the **same split**
    so that evidence never crosses a fold boundary. A variant with no same-gene same-label
    partner gets no `same_gene` entry, and the caller must skip that arm for it rather than
    silently substituting a different gene -- `pair_by_gene` sampling makes this empty in
    practice, but a set built without it will hit this.
    """
    rng = random.Random(seed)
    out: dict[str, dict[str, str]] = {}

    by_split: dict[str, list[dict]] = {}
    for v in variants:
        by_split.setdefault(v["split"], []).append(v)

    for split, group in by_split.items():
        by_gene_label: dict[tuple[str, str], list[dict]] = {}
        by_label: dict[str, list[dict]] = {}
        for v in group:
            by_gene_label.setdefault((v["gene"], v["label"]), []).append(v)
            by_label.setdefault(v["label"], []).append(v)

        for v in group:
            vid = str(v["variation_id"])
            picks: dict[str, str] = {}

            same = [
                o for o in by_gene_label[(v["gene"], v["label"])]
                if str(o["variation_id"]) != vid
            ]
            if same:
                picks["same_gene"] = str(rng.choice(same)["variation_id"])

            other = [o for o in by_label[v["label"]] if o["gene"] != v["gene"]]
            if other:
                picks["other_gene"] = str(rng.choice(other)["variation_id"])

            out[vid] = picks

    return out


_VARIANT_MENTION = re.compile(r"\b(rs\d{4,}|c\.\d+[ACGT>_a-z]|p\.[A-Z][a-z]{2}\d+)")


def describe_distractor(
    target_passages: list[dict],
    donor_passages: list[dict],
    donor_keys: list[str] | None = None,
) -> dict:
    """Characterise how usable one distractor actually is.

    Two failure modes, both measured on dev and neither obvious from the prompt:

    * `identical_to_grounded` -- two variants in the same gene can retrieve the same
      passages, in which case the "distractor" arm silently *is* the grounded arm. Measured
      at 10/150 on dev. These must be dropped, not averaged in: they pull the arm's effect
      toward zero for a mechanical reason.
    * `names_a_variant` / `names_donor` -- the model can only notice that evidence is about
      the wrong variant if the evidence names a variant at all. Only 15% of same-gene
      distractor evidence named its donor on dev. Where nothing is named, generic gene-level
      text is not detectably wrong for the target, and the arm is not testing conflation.
      Phase 6/7 must stratify on this rather than pooling.
    """
    donor_text = " ".join(p["text"] for p in donor_passages)
    return {
        "identical_to_grounded": [p["text"] for p in target_passages]
        == [p["text"] for p in donor_passages],
        "names_a_variant": bool(_VARIANT_MENTION.search(donor_text)),
        "names_donor": bool(donor_keys) and any(k and k in donor_text for k in donor_keys),
    }


def donor_keys(variant: dict) -> list[str]:
    """Identifiers by which a donor variant might be named in its own evidence."""
    keys: list[str] = []
    rsid = variant.get("rsid")
    if rsid and str(rsid) != "nan":
        keys.append(str(rsid))
    name = str(variant.get("Name") or "")
    for pat in (r"\((p\.[A-Za-z0-9*=]+)\)", r":(c\.[A-Za-z0-9_>+*-]+)"):
        m = re.search(pat, name)
        if m:
            keys.append(m.group(1))
    return keys


def coverage(assignment: dict[str, dict[str, str]]) -> dict[str, float]:
    """Fraction of variants that have each distractor type available."""
    n = len(assignment) or 1
    return {
        "same_gene": sum(1 for a in assignment.values() if "same_gene" in a) / n,
        "other_gene": sum(1 for a in assignment.values() if "other_gene" in a) / n,
        "n": len(assignment),
    }
