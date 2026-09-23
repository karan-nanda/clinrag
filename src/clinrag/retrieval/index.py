"""Phase 3: one evidence record type, BM25 ranking, and the evidence budget.

Two sources feed the pool and they are not interchangeable:

* `pubtator` -- title/abstract passages. Mostly **gene-level**: on a 25-variant probe, 92% of
  variants had no passage naming their specific variant.
* `clinvar_comment` -- sanitized submitter notes. **Variant-level** by construction, and the
  only variant-level source most variants have. Also the source that states the answer, which
  is why nothing reaches here unsanitized.

The evidence budget is not a convenience. Pathogenic variants carry ~3.3x more sanitized text
than benign ones, so an unbounded pool hands the model a length cue that correlates with the
label. Capping both passage count and character count equalizes what each arm actually
receives; `budget_report` exists to prove it did.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+(?:[.>][a-z0-9]+)*")


@dataclass
class Evidence:
    """One retrievable unit, from either source."""

    doc_id: str
    source: str                  # "pubtator" | "clinvar_comment"
    text: str
    level: str = "other"         # "variant" | "gene" | "other"
    year: int | None = None
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, keeping rsIDs and HGVS notation intact.

    `c.1067A>G` and `rs80357906` are the highest-signal tokens in this domain; a naive
    `\\w+` split shatters HGVS into meaningless fragments.
    """
    return _TOKEN.findall(text.lower())


def variant_query(
    gene: str,
    rsid: str | None = None,
    hgvs: str | None = None,
    protein: str | None = None,
    phenotypes: str | None = None,
) -> str:
    """Build the retrieval query for one variant from the fields we hold."""
    parts = [gene]
    for extra in (rsid, hgvs, protein, phenotypes):
        if extra:
            parts.append(str(extra))
    return " ".join(parts)


class BM25Retriever:
    """BM25 over an evidence pool. The plan's baseline retriever, and a strong one."""

    def __init__(self, pool: list[Evidence]):
        self.pool = pool
        self._corpus = [tokenize(e.text) for e in pool]
        # BM25Okapi divides by corpus length; an empty pool must not raise here.
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    # Tie-break priority when BM25 cannot discriminate.
    _LEVEL_RANK = {"variant": 0, "gene": 1, "other": 2}

    def search(self, query: str, k: int = 10) -> list[tuple[Evidence, float]]:
        """Rank the pool. Ties are broken deterministically, variant-level evidence first.

        The tie-break is not cosmetic. Our pools are already gene-filtered, so the gene
        symbol appears in nearly every document and its IDF collapses toward zero -- in the
        degenerate case where every query term sits in half the corpus, `BM25Okapi` returns
        an IDF of exactly 0 for all of them and *every document scores 0*. Ranking then falls
        back to insertion order, which is luck. Ordering ties by (level, doc_id) makes the
        result reproducible across runs -- required for temperature-0 generation -- and puts
        variant-level evidence ahead of gene-level when the scores say nothing.
        """
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(
            zip(self.pool, scores),
            key=lambda t: (-t[1], self._LEVEL_RANK.get(t[0].level, 3), t[0].doc_id),
        )
        return ranked[:k]


def apply_budget(
    ranked: list[tuple[Evidence, float]],
    k: int = 10,
    max_chars: int = 6000,
    variant_level_slots: int = 1,
) -> list[Evidence]:
    """Take the top `k` passages, stopping early if `max_chars` would be exceeded.

    `variant_level_slots` reserves the first slots for variant-level evidence, and caps it at
    the same number. Both halves matter:

    * **Reserve.** Left to pure BM25, variant-level submitter comments get buried under
      gene-level abstracts that match the gene token more often -- measured at 0.7 of 7.4
      delivered passages. The `grounded` arm would then rarely contain evidence about the
      actual variant, which is the whole premise of the arm.
    * **Cap.** Pathogenic variants have more comments than benign ones, so an uncapped
      reserve re-introduces the label cue in passage counts that the character budget just
      closed. Capping at a number every variant can meet keeps the arms comparable. Every
      variant has >=1 sanitized comment by construction, so a cap of 1 is exactly balanced;
      raising it trades balance for richer evidence, and the budget report will show the cost.

    A passage that would overflow `max_chars` is skipped rather than truncated -- a
    half-sentence is not evidence, and truncating mid-claim would manufacture `unsupported`
    verdicts in Phase 5.
    """
    out: list[Evidence] = []
    used = 0

    def take(ev: Evidence) -> bool:
        nonlocal used
        if len(out) >= k or used + len(ev.text) > max_chars:
            return False
        out.append(ev)
        used += len(ev.text)
        return True

    if variant_level_slots > 0:
        taken = 0
        for ev, _ in ranked:
            if taken >= variant_level_slots:
                break
            if ev.level == "variant" and take(ev):
                taken += 1

    chosen = {id(e) for e in out}
    for ev, _ in ranked:
        if id(ev) in chosen:
            continue
        # The cap: no further variant-level evidence beyond the reserved slots.
        if variant_level_slots > 0 and ev.level == "variant":
            continue
        take(ev)
    return out


def budget_report(delivered: dict[str, list[Evidence]], labels: dict[str, str]) -> dict:
    """Check that delivered evidence does not differ in volume by label.

    `delivered` maps variant id -> evidence actually placed in the prompt; `labels` maps
    variant id -> "pathogenic"/"benign". If the two classes still differ materially in
    characters delivered, the length cue survived the budget and `max_chars` is too loose.
    """
    stats: dict[str, dict[str, float]] = {}
    for label in sorted(set(labels.values())):
        ids = [v for v, lab in labels.items() if lab == label and v in delivered]
        if not ids:
            continue
        chars = [sum(len(e.text) for e in delivered[v]) for v in ids]
        counts = [len(delivered[v]) for v in ids]
        stats[label] = {
            "n_variants": len(ids),
            "mean_passages": round(sum(counts) / len(counts), 2),
            "mean_chars": round(sum(chars) / len(chars), 1),
        }
    if len(stats) == 2:
        a, b = stats.values()
        hi, lo = max(a["mean_chars"], b["mean_chars"]), min(a["mean_chars"], b["mean_chars"])
        stats["char_ratio"] = round(hi / lo, 2) if lo else float("inf")
    return stats


def recall_at_k(
    retrieved: list[Evidence],
    relevant_ids: set[str],
    k: int | None = None,
) -> float:
    """Fraction of known-relevant documents present in the top-k.

    Phase 3 must be evaluable on its own. Without this, a weak `grounded` arm cannot be
    attributed to retrieval failure versus generation failure.
    """
    if not relevant_ids:
        return float("nan")
    top = retrieved[: k or len(retrieved)]
    found = {e.doc_id for e in top} & relevant_ids
    return len(found) / len(relevant_ids)
