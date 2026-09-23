"""Phase 3: a retrieval gold set derived from expert citations.

When a ClinVar submitter cites a PMID in support of a variant classification, a domain expert
has judged that paper relevant to that specific variant. That is a relevance label, produced
independently of our retriever and of PubTator's entity linking, and it costs nothing.

**The gold set is extracted from the RAW descriptions, before sanitation.** That is
deliberate and safe: gold labels are used only to score retrieval and never enter a prompt.
Sanitation strips sentences that state the classification, and those sentences carry many of
the citations -- using raw text lifts coverage from 380 to 486 variants.

Two known biases, both of which belong in the paper:

1. **Label skew.** 462 of the 486 covered variants are pathogenic. Curators cite literature
   to justify pathogenicity; benign calls rest on population frequency and cite nothing.
   Use this set to measure retrieval quality, never to compare retrieval *across labels*.
2. **Discovery bias.** It rewards finding the papers curators already found, so it cannot
   credit a retriever that surfaces genuinely relevant work the curator missed. It is a
   lower bound on recall, not a ceiling on quality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

# ClinVar comments write citations as "PMID: 12345678", "PMID 12345678", "(PMID: 1234567)"
# and, for several papers at once, "PMIDs: 111, 222". The plural form is common enough that
# missing it silently drops gold labels.
_PMID = re.compile(r"PMIDs?\s*:?\s*([0-9]{5,8})", re.IGNORECASE)
_PMID_RUN = re.compile(r"PMIDs?\s*:?\s*((?:[0-9]{5,8}[,\s;]*)+)", re.IGNORECASE)


def extract_pmids(text: str) -> set[str]:
    """All PMIDs cited in one description, including comma-joined runs."""
    out: set[str] = set(_PMID.findall(text or ""))
    for run in _PMID_RUN.findall(text or ""):
        out.update(re.findall(r"[0-9]{5,8}", run))
    return out


def build(comments: pd.DataFrame, text_col: str = "Description") -> dict[str, set[str]]:
    """variant id -> set of expert-cited PMIDs. Variants citing nothing are omitted."""
    gold: dict[str, set[str]] = {}
    for vid, grp in comments.groupby("VariationID"):
        pmids: set[str] = set()
        for t in grp[text_col]:
            pmids |= extract_pmids(str(t))
        if pmids:
            gold[str(vid)] = pmids
    return gold


@dataclass
class RetrievalScore:
    """Retrieval measured in two stages, because the two failures need different fixes."""

    variants: int = 0
    pool_recall: float = 0.0          # cited paper present in the candidate pool at all
    recall_at_k: dict[int, float] = None  # cited paper delivered in the top-k
    gold_pmids: int = 0
    reachable_pmids: int = 0

    def summary(self) -> str:
        ks = ", ".join(f"recall@{k}={v:.3f}" for k, v in sorted((self.recall_at_k or {}).items()))
        return (
            f"variants={self.variants}  gold_pmids={self.gold_pmids}  "
            f"pool_recall={self.pool_recall:.3f}  {ks}"
        )


def score(
    per_variant: list[tuple[set[str], set[str], list[set[str]]]],
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> RetrievalScore:
    """Score retrieval from (gold_pmids, pool_pmids, ranked_pmids_per_position) triples.

    `pool_recall` asks whether the cited paper is in the candidate pool at all -- a corpus
    problem, fixed by broadening the search. `recall_at_k` asks whether ranking surfaced it
    -- a ranking problem, fixed by a better retriever. Reporting only the second makes a
    corpus failure look like a retriever failure, which is the confusion Phase 3 exists to
    prevent.
    """
    pool_hits = pool_total = 0
    at_k = {k: [0, 0] for k in ks}  # [hits, total]
    gold_total = reachable = 0

    for gold, pool, ranked in per_variant:
        if not gold:
            continue
        gold_total += len(gold)
        in_pool = gold & pool
        reachable += len(in_pool)
        pool_hits += len(in_pool)
        pool_total += len(gold)

        for k in ks:
            topk: set[str] = set()
            for s in ranked[:k]:
                topk |= s
            # Score ranking against what is actually reachable: penalising the ranker for a
            # paper absent from the pool double-counts the corpus failure.
            denom = len(in_pool)
            if denom:
                at_k[k][0] += len(in_pool & topk)
                at_k[k][1] += denom

    return RetrievalScore(
        variants=sum(1 for g, _, _ in per_variant if g),
        pool_recall=(pool_hits / pool_total) if pool_total else float("nan"),
        recall_at_k={k: (h / t if t else float("nan")) for k, (h, t) in at_k.items()},
        gold_pmids=gold_total,
        reachable_pmids=reachable,
    )
