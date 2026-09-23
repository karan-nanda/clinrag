"""Leak removal and near-duplicate collapse for the evidence pool.

Two hazards, both documented in Li et al. 2026 (see docs/related_work.md §1):

1. **Label leakage.** ClinVar submitter comments state the classification outright, and our
   prompt already hands the model that classification. Left in, the `grounded` arm is partly
   reading the answer back to us and the headline comparison is worthless. Li et al. removed
   ~25% of text this way with a trained sentence classifier; this module does it with rules
   and reports its own drop rate so the two can be compared.

2. **Template duplication.** Labs generate summaries from standard templates, so a pool can
   hold twenty near-identical sentences. A claim `supported` by twenty copies of one sentence
   is supported once, and retrieval depth `k` is meaningless over a duplicated pool. Li et al.
   deduplicated with MinHash at >95% similarity; so do we.

The rule-based sanitizer is v1 and deliberately errs toward over-removal. The upgrade path is
a trained classifier, as in the paper -- but the residual-leak caveat survives either way and
belongs in the limitations section regardless.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Sentences asserting a classification. Our prompt supplies the label, so any sentence
# restating it is pure leakage.
_LABEL_TERMS = re.compile(
    r"\b(?:likely\s+)?(?:pathogenic|benign)\b"
    r"|\bvariant\s+of\s+uncertain\s+significance\b"
    r"|\buncertain\s+significance\b"
    r"|\bVUS\b",
    re.IGNORECASE,
)

# Verdict / conclusion framing, independent of whether the label word appears.
_VERDICT = re.compile(
    r"\b(?:classif\w*|interpret\w*|categoriz\w*)\s+(?:as|to\s+be)\b"
    r"|\bthis\s+(?:variant|alteration|change|sequence\s+change)\s+is\s+"
    r"(?:considered|classified|interpreted|reported|therefore)\b"
    r"|\bwe\s+(?:classify|interpret|consider|conclude)\b"
    r"|\bmeets?\s+(?:the\s+)?criteria\b"
    r"|\b(?:in\s+summary|in\s+conclusion|taken\s+together|collectively)\b",
    re.IGNORECASE,
)

# ACMG/AMP evidence codes are a direct label proxy: PVS1 means pathogenic, BA1 means benign.
_ACMG_CODE = re.compile(r"\b(?:PVS1|PS[1-4]|PM[1-6]|PP[1-5]|BA1|BS[1-4]|BP[1-7])\b")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


@dataclass
class SanitationReport:
    sentences_in: int = 0
    sentences_out: int = 0
    dropped_label: int = 0
    dropped_verdict: int = 0
    dropped_acmg: int = 0

    @property
    def drop_rate(self) -> float:
        return 0.0 if not self.sentences_in else 1 - self.sentences_out / self.sentences_in

    def as_dict(self) -> dict:
        return {
            "sentences_in": self.sentences_in,
            "sentences_out": self.sentences_out,
            "drop_rate": round(self.drop_rate, 4),
            "dropped_label": self.dropped_label,
            "dropped_verdict": self.dropped_verdict,
            "dropped_acmg": self.dropped_acmg,
        }


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def sanitize(text: str, report: SanitationReport | None = None) -> str:
    """Drop every sentence that asserts or encodes the classification.

    Returns the surviving evidence sentences joined back into a passage. May return "" when
    a comment is nothing but verdict -- callers must handle the empty result rather than
    indexing an empty passage.
    """
    rep = report if report is not None else SanitationReport()
    kept: list[str] = []
    for sent in split_sentences(text):
        rep.sentences_in += 1
        if _ACMG_CODE.search(sent):
            rep.dropped_acmg += 1
            continue
        if _LABEL_TERMS.search(sent):
            rep.dropped_label += 1
            continue
        if _VERDICT.search(sent):
            rep.dropped_verdict += 1
            continue
        kept.append(sent)
        rep.sentences_out += 1
    return " ".join(kept)


# --------------------------------------------------------------------------- dedup


def _shingles(text: str, k: int = 5) -> set[str]:
    """Word-level k-shingles. Word shingles beat character shingles for template text,
    where boilerplate differs only in a variant name."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _signature(shingles: set[str], n_perm: int = 64) -> tuple[int, ...]:
    """MinHash signature. Each 'permutation' is a distinct salted hash."""
    if not shingles:
        return tuple([0] * n_perm)
    sig = []
    for i in range(n_perm):
        salt = str(i).encode()
        sig.append(
            min(
                int.from_bytes(hashlib.blake2b(salt + s.encode(), digest_size=8).digest(), "big")
                for s in shingles
            )
        )
    return tuple(sig)


def _similarity(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def dedupe(
    texts: list[str],
    threshold: float = 0.95,
    n_perm: int = 128,
    k: int = 5,
    exact_below: int = 500,
) -> list[int]:
    """Return indices of texts to KEEP, collapsing near-duplicates above `threshold`.

    The first occurrence of a duplicate group wins.

    Below `exact_below` texts the comparison is exact Jaccard over word shingles. MinHash
    with `n_perm` permutations has a standard error around 1/sqrt(n_perm) -- roughly +/-0.09
    at 128 perms -- which is too coarse to sit a 0.95 cut on, and measured error on short
    texts was 0.116. Per-variant pools are small, so exactness is affordable; MinHash is the
    fallback for large pools only.
    """
    shingles = [_shingles(t, k=k) for t in texts]
    keep: list[int] = []

    if len(texts) < exact_below:
        for i, sh in enumerate(shingles):
            if any(_jaccard(sh, shingles[j]) >= threshold for j in keep):
                continue
            keep.append(i)
        return keep

    sigs = [_signature(sh, n_perm=n_perm) for sh in shingles]
    for i, sig in enumerate(sigs):
        if any(_similarity(sig, sigs[j]) >= threshold for j in keep):
            continue
        keep.append(i)
    return keep
