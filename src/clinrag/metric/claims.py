"""Phase 5: decompose an explanation into atomic, independently checkable claims.

Every claim carries two labels beyond its text, and both exist to make Phase 7 measurable
rather than anecdotal:

* **scope** -- does the claim assert something about *this specific variant*, or about the
  gene/disease in general? This is what makes gene/variant conflation detectable: a
  `variant`-scoped claim whose only support is `gene`-level evidence is the conflation
  pattern, and it is clinically significant because it reads as variant-specific authority.
* **claim_type** -- which kind of assertion it is. The types map onto the proposed taxonomy
  categories (invented population frequencies, fabricated inheritance patterns, citations to
  studies that do not exist), so category rates fall out of the metric instead of needing a
  separate pass.

Two decomposers are provided. `SentenceDecomposer` is rule-based, deterministic and free; it
is the baseline and the test fixture. `LLMDecomposer` is the real one. Decomposition quality
is itself a validity threat -- if the decomposer splits badly, every downstream number is
wrong -- so the human validation in `validation.py` must cover decomposition, not only
verification.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

SCOPES = ("variant", "gene", "disease", "other")

CLAIM_TYPES = (
    "population_frequency",
    "functional",
    "case_observation",
    "inheritance",
    "citation",
    "conservation",
    "mechanism",
    "other",
)


@dataclass
class Claim:
    """One atomic assertion lifted out of an explanation."""

    claim_id: str
    variation_id: str
    arm: str
    model: str
    text: str
    scope: str = "other"
    claim_type: str = "other"
    source_sentence: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------- typing

# Expressions by which a sentence refers to THIS variant rather than the gene at large.
_VARIANT_REF = re.compile(
    r"\bthis\s+(?:variant|allele|change|substitution|mutation|missense\s+change|"
    r"sequence\s+change|nucleotide\s+change|deletion|insertion|duplication)\b"
    r"|\bthe\s+(?:variant|substitution|sequence\s+change)\b"
    r"|\brs\d{4,}\b"
    r"|\bc\.\d+"
    r"|\bp\.[A-Z][a-z]{2}\d+",
    re.IGNORECASE,
)

_DISEASE_REF = re.compile(
    r"\b(?:syndrome|disease|disorder|cancer|carcinoma|deficiency|dystrophy|"
    r"phenotype|condition|patients\s+with)\b",
    re.IGNORECASE,
)

_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("citation", re.compile(r"\bPMID\b|\bet\s+al\b|\b(?:19|20)\d{2}\)|\bpublished\b", re.I)),
    ("population_frequency", re.compile(
        r"\bgnomAD\b|\bExAC\b|\b1000\s*Genomes\b|allele\s+frequenc|minor\s+allele|"
        r"\bMAF\b|population\s+databases?|\d+(?:\.\d+)?\s*%|absent\s+from\s+controls", re.I)),
    ("inheritance", re.compile(
        r"\bautosomal\s+(?:dominant|recessive)\b|\bX-linked\b|\bde\s+novo\b|"
        r"\bsegregat\w+|\bhomozygous\b|\bheterozygous\b|\bcompound\s+heterozyg\w+|"
        r"\bpenetrance\b|\binheritance\b", re.I)),
    ("functional", re.compile(
        r"\bfunctional\s+(?:assay|stud|screen|data)|\bin\s+vitro\b|\bloss\s+of\s+function\b|"
        r"\benzyme\s+activity\b|\bprotein\s+function\b|\bimpair\w+|\babolish\w+|"
        r"\bexperimental\s+stud\w+", re.I)),
    ("mechanism", re.compile(
        r"\bsplic\w+|\bnonsense[- ]mediated\s+decay\b|\bNMD\b|\btruncat\w+|"
        r"\bframeshift\b|\bpremature\s+(?:stop|termination)\b|\bstop\s+codon\b|"
        r"\breading\s+frame\b", re.I)),
    ("conservation", re.compile(
        r"\bconserv\w+|\bevolutionar\w+|\bortholog\w+|\bPhyloP\b|\bGERP\b|"
        r"\bphysicochemical\b|\bin\s+silico\b|\bcomputational\s+predict\w+", re.I)),
    ("case_observation", re.compile(
        r"\breported\s+in\b|\bobserved\s+in\b|\bidentified\s+in\b|\bdescribed\s+in\b|"
        r"\bindividuals?\s+(?:with|affected)\b|\bfamil(?:y|ies)\b|\bprobands?\b|"
        r"\bcohorts?\b|\bcases?\b", re.I)),
]


def classify_scope(text: str) -> str:
    """Whether the claim is about this variant, the gene, the disease, or none of those."""
    if _VARIANT_REF.search(text):
        return "variant"
    if _DISEASE_REF.search(text) and not re.search(r"\bgene\b", text, re.I):
        return "disease"
    if re.search(r"\bgene\b|\bprotein\b|\b[A-Z][A-Z0-9]{2,}\b", text):
        return "gene"
    return "other"


def classify_type(text: str) -> str:
    """First matching type wins; ordering puts the most specific patterns first.

    `citation` is checked before everything else because a fabricated citation is a distinct
    taxonomy category regardless of what the citing sentence otherwise asserts.
    """
    for name, pattern in _TYPE_PATTERNS:
        if pattern.search(text):
            return name
    return "other"


# ---------------------------------------------------------------- decomposers

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_MIN_CLAIM_CHARS = 15


class SentenceDecomposer:
    """Deterministic baseline: one sentence, one claim.

    Free, reproducible, and a genuine baseline rather than only a stub -- reporting the
    metric under both decomposers shows how much the headline depends on the LLM
    decomposition step, which is a reviewer's first question.

    It does NOT split conjoined assertions ("X is conserved and absent from gnomAD" stays as
    one claim), so it under-counts atomicity. That direction of error is at least predictable.
    """

    name = "sentence"

    def decompose(self, text: str, variation_id: str, arm: str, model: str) -> list[Claim]:
        claims: list[Claim] = []
        for i, sent in enumerate(_SENT_SPLIT.split(text.strip())):
            sent = sent.strip()
            if len(sent) < _MIN_CLAIM_CHARS:
                continue
            claims.append(
                Claim(
                    claim_id=f"{variation_id}:{arm}:{i}",
                    variation_id=variation_id,
                    arm=arm,
                    model=model,
                    text=sent,
                    scope=classify_scope(sent),
                    claim_type=classify_type(sent),
                    source_sentence=i,
                )
            )
        return claims


DECOMPOSE_SYSTEM = (
    "You split clinical variant explanations into atomic factual claims. "
    "An atomic claim asserts exactly one checkable fact. Split conjoined assertions into "
    "separate claims. Resolve pronouns and vague references so each claim stands alone when "
    "read in isolation. Copy the assertion faithfully; never add, infer, or soften anything."
)

DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "scope": {"type": "string", "enum": list(SCOPES)},
                    "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
                    "source_sentence": {"type": "integer"},
                },
                "required": ["text", "scope", "claim_type", "source_sentence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

DECOMPOSE_INSTRUCTIONS = """Split the explanation below into atomic claims.

For each claim also record:
  scope       - "variant" if it asserts something about THIS specific variant;
                "gene" if about the gene in general; "disease" if about the condition;
                "other" otherwise.
  claim_type  - one of: population_frequency, functional, case_observation, inheritance,
                citation, conservation, mechanism, other.
  source_sentence - 0-based index of the sentence the claim came from.

EXPLANATION
{explanation}"""


class LLMDecomposer:
    """Model-backed decomposition with a constrained output schema.

    Uses structured outputs so the response validates against `DECOMPOSE_SCHEMA` rather than
    being parsed out of prose. No temperature is set -- current models reject it.
    """

    def __init__(self, model: str = "claude-opus-5", effort: str = "medium"):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.name = f"llm:{model}"

    def decompose(self, text: str, variation_id: str, arm: str, model: str) -> list[Claim]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": DECOMPOSE_SCHEMA},
            },
            system=DECOMPOSE_SYSTEM,
            messages=[{
                "role": "user",
                "content": DECOMPOSE_INSTRUCTIONS.format(explanation=text),
            }],
        )
        return parse_claims(_response_json(resp), variation_id, arm, model)


def parse_claims(payload: dict, variation_id: str, arm: str, model: str) -> list[Claim]:
    """Build Claims from a decomposer's JSON payload, dropping malformed entries."""
    out: list[Claim] = []
    for i, item in enumerate(payload.get("claims", [])):
        text = (item.get("text") or "").strip()
        if len(text) < _MIN_CLAIM_CHARS:
            continue
        scope = item.get("scope")
        ctype = item.get("claim_type")
        out.append(
            Claim(
                claim_id=f"{variation_id}:{arm}:{i}",
                variation_id=variation_id,
                arm=arm,
                model=model,
                text=text,
                scope=scope if scope in SCOPES else classify_scope(text),
                claim_type=ctype if ctype in CLAIM_TYPES else classify_type(text),
                source_sentence=int(item.get("source_sentence", i) or 0),
            )
        )
    return out


def _response_json(resp) -> dict:
    import json

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return json.loads(text)
