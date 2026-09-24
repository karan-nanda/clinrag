"""Phase 5: verify each atomic claim against the evidence pool.

Three labels, and the distinction between the middle one and the last one is the point of
the whole study:

* `supported`    -- some passage entails the claim.
* `unsupported`  -- no passage entails or contradicts it. **The claim may well be true.**
  This is not a synonym for "hallucinated" and must never be reported as one.
* `contradicted` -- some passage contradicts it.

On top of the label, each verdict carries `conflation`: True when a **variant**-scoped claim
is supported *only* by **gene**-level passages. The claim is then technically "supported" by
the corpus while asserting variant-specific authority the evidence does not carry. That is
the clinically significant failure this project exists to measure, and it is invisible to a
plain supported/unsupported metric.

**Judge self-preference is a live confound.** An LLM judge tends to favour text from its own
family, and one of our generators is Claude. Every verdict therefore records the judge model
alongside the generator model, `same_family_as_generator` flags the overlap, and the human
validation in `validation.py` must over-sample those cases. Prefer a judge from a different
family to the generator being judged wherever the budget allows.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

LABELS = ("supported", "unsupported", "contradicted")


@dataclass
class Verdict:
    """The outcome of checking one claim against one evidence pool."""

    claim_id: str
    label: str
    supporting_doc_ids: list[str] = field(default_factory=list)
    contradicting_doc_ids: list[str] = field(default_factory=list)
    judge: str = ""
    rationale: str = ""
    conflation: bool = False
    same_family_as_generator: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def detect_conflation(claim, verdict: Verdict, pool: list[dict]) -> bool:
    """A variant-specific claim supported only by gene-level evidence.

    Requires an actually-supported claim: an unsupported claim is already counted elsewhere,
    and calling it conflation too would double-count the same failure.
    """
    if claim.scope != "variant" or verdict.label != "supported":
        return False
    if not verdict.supporting_doc_ids:
        return False
    levels = {p.get("level") for p in pool if p.get("doc_id") in set(verdict.supporting_doc_ids)}
    if not levels:
        return False
    return "variant" not in levels


def _finalise(claim, verdict: Verdict, pool: list[dict], generator_model: str) -> Verdict:
    verdict.conflation = detect_conflation(claim, verdict, pool)
    verdict.same_family_as_generator = _same_family(verdict.judge, generator_model)
    return verdict


def _same_family(judge: str, generator: str) -> bool:
    """Crude family check: enough to flag the confound for stratified human review."""
    if not judge or not generator:
        return False

    def fam(m: str) -> str:
        m = m.lower()
        for key in ("claude", "gpt", "llama", "mistral", "gemini", "qwen"):
            if key in m:
                return key
        return m.split("-")[0]

    return fam(judge) == fam(generator)


# ------------------------------------------------------------------ baseline

_STOP = {
    "this", "that", "the", "a", "an", "is", "are", "was", "were", "of", "in", "and", "or",
    "to", "for", "with", "has", "have", "been", "be", "it", "its", "as", "at", "by", "on",
    "from", "which", "not", "no", "variant", "gene", "protein",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9.>]+", text.lower()) if w not in _STOP and len(w) > 2}


class LexicalVerifier:
    """Token-overlap baseline. Deterministic, free, and deliberately weak.

    It exists for two reasons: to exercise the pipeline without API spend, and to give the
    paper a floor. If an LLM judge cannot beat token overlap against human labels, the judge
    is not earning its cost. It cannot detect contradiction at all -- it returns only
    `supported` or `unsupported` -- which is itself the argument for a real entailment model.
    """

    name = "lexical-overlap"

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def verify(self, claim, pool: list[dict], generator_model: str = "") -> Verdict:
        words = _content_words(claim.text)
        support: list[str] = []
        if words:
            for p in pool:
                overlap = len(words & _content_words(p.get("text", ""))) / len(words)
                if overlap >= self.threshold:
                    support.append(p.get("doc_id", ""))

        v = Verdict(
            claim_id=claim.claim_id,
            label="supported" if support else "unsupported",
            supporting_doc_ids=support,
            judge=self.name,
            rationale=f"token overlap >= {self.threshold}",
        )
        return _finalise(claim, v, pool, generator_model)


# ----------------------------------------------------------------- LLM judge

JUDGE_SYSTEM = (
    "You verify individual factual claims against a fixed set of evidence passages, as a "
    "careful clinical geneticist would. You judge ONLY what the passages say. "
    "Your own knowledge of genetics must not influence the verdict: a claim you believe is "
    "true but that the passages do not state is 'unsupported', not 'supported'. "
    "'unsupported' is not an accusation of falsehood; it records absence of evidence."
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "supporting_passages": {"type": "array", "items": {"type": "integer"}},
        "contradicting_passages": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
    "required": ["label", "supporting_passages", "contradicting_passages", "rationale"],
    "additionalProperties": False,
}

JUDGE_INSTRUCTIONS = """CLAIM
{claim}

EVIDENCE PASSAGES
{passages}

Decide whether the passages support, contradict, or neither, for this claim.

  supported     - at least one passage states or directly entails the claim
  contradicted  - at least one passage asserts something incompatible with it
  unsupported   - neither; the passages are silent on it

List the numbers of the passages that drive your verdict. If the verdict is 'unsupported',
both lists must be empty. Keep the rationale to one sentence."""


class LLMJudge:
    """Model-backed verification with a constrained output schema.

    Passage indices are returned rather than free-text quotes so support can be traced to
    specific documents -- which is what makes conflation detection possible.
    """

    def __init__(self, model: str = "claude-opus-5", effort: str = "medium"):
        from clinrag.generation.backends import make_client

        self.client = make_client()
        self.model = model
        self.effort = effort
        self.name = f"llm:{model}"

    def verify(self, claim, pool: list[dict], generator_model: str = "") -> Verdict:
        numbered = "\n\n".join(
            f"[{i}] {p.get('text', '').strip()}" for i, p in enumerate(pool)
        ) or "(no passages provided)"

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
            },
            system=JUDGE_SYSTEM,
            messages=[{
                "role": "user",
                "content": JUDGE_INSTRUCTIONS.format(claim=claim.text, passages=numbered),
            }],
        )
        payload = json.loads(
            "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        )
        return verdict_from_payload(payload, claim, pool, self.name, generator_model)


def verdict_from_payload(
    payload: dict,
    claim,
    pool: list[dict],
    judge: str,
    generator_model: str = "",
) -> Verdict:
    """Build a Verdict from a judge's JSON, resolving passage indices to doc ids."""

    def ids(key: str) -> list[str]:
        out = []
        for i in payload.get(key, []) or []:
            if isinstance(i, int) and 0 <= i < len(pool):
                out.append(pool[i].get("doc_id", ""))
        return out

    label = payload.get("label")
    if label not in LABELS:
        label = "unsupported"

    support = ids("supporting_passages")
    contradict = ids("contradicting_passages")

    # A judge that says "supported" while citing nothing has not shown its work. Treat it as
    # unsupported rather than trusting an unevidenced assertion of support.
    if label == "supported" and not support:
        label = "unsupported"
    if label == "contradicted" and not contradict:
        label = "unsupported"
    if label == "unsupported":
        support, contradict = [], []

    v = Verdict(
        claim_id=claim.claim_id,
        label=label,
        supporting_doc_ids=support,
        contradicting_doc_ids=contradict,
        judge=judge,
        rationale=str(payload.get("rationale", ""))[:500],
    )
    return _finalise(claim, v, pool, generator_model)
