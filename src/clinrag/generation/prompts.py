"""Phase 4: prompt construction for the four arms.

The single most important property here is that **prompt structure is identical across
arms**. Only the contents of the EVIDENCE block change. If the instruction text differed by
arm, any difference in hallucination rate could be the instruction rather than the grounding,
and the experiment would measure nothing.

That is why the `ungrounded` arm still carries an EVIDENCE header, reading `(none provided)`,
rather than dropping the section: removing a section changes the shape of the prompt.

The task instruction deliberately does NOT say "use only the evidence provided". Such an
instruction would suppress unsupported claims directly, which is the quantity we are trying
to measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ARMS = ("grounded", "ungrounded", "distractor_same_gene", "distractor_other_gene")

SYSTEM = (
    "You are a clinical molecular geneticist writing the evidence rationale that accompanies "
    "a variant classification in a diagnostic report. Write for another clinical geneticist. "
    "Be specific and concrete. Do not hedge unnecessarily."
)

TASK = (
    "Explain why this variant carries the classification shown above.\n"
    "Write 4-8 sentences of continuous prose. Each sentence should make one checkable "
    "statement about this variant, its gene, or the associated condition. "
    "Do not restate the classification itself."
)

_TEMPLATE = """VARIANT
Gene: {gene}
HGVS: {name}
dbSNP: {rsid}
Genomic position (GRCh38): chr{chrom}:{pos} {ref}>{alt}
Reported condition(s): {phenotypes}

CLASSIFICATION
{label}

EVIDENCE
{evidence}

TASK
{task}"""

NO_EVIDENCE = "(none provided)"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    arm: str
    variation_id: str
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "system": self.system,
            "user": self.user,
            "arm": self.arm,
            "variation_id": self.variation_id,
            "meta": self.meta,
        }


def format_evidence(passages: list[dict]) -> str:
    """Render the evidence block.

    Passages are numbered so claims can be traced back in Phase 5, and each carries its
    source and year. The `level` tag is deliberately NOT shown to the model -- telling it
    which passages are variant-level versus gene-level would hand it the very distinction
    the gene/variant conflation category is meant to test.
    """
    if not passages:
        return NO_EVIDENCE
    out = []
    for i, p in enumerate(passages, 1):
        src = p.get("source", "?")
        year = p.get("year")
        tag = f"[{i}] ({src}{', ' + str(year) if year else ''})"
        out.append(f"{tag} {p['text'].strip()}")
    return "\n\n".join(out)


def build(
    variant: dict,
    arm: str,
    passages: list[dict] | None = None,
    meta: dict | None = None,
) -> Prompt:
    """Build the prompt for one variant under one arm."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    if arm == "ungrounded" and passages:
        raise ValueError("the ungrounded arm must receive no passages")
    if arm != "ungrounded" and not passages:
        raise ValueError(f"arm {arm!r} requires passages; got none")

    user = _TEMPLATE.format(
        gene=variant.get("gene") or "unknown",
        name=variant.get("Name") or "not provided",
        rsid=variant.get("rsid") or "not provided",
        chrom=variant.get("chrom") or "?",
        pos=variant.get("pos") or "?",
        ref=variant.get("ref") or "?",
        alt=variant.get("alt") or "?",
        phenotypes=(variant.get("phenotypes") or "not specified").replace("|", "; "),
        label=_label_text(variant["label"]),
        evidence=format_evidence(passages or []),
        task=TASK,
    )
    return Prompt(
        system=SYSTEM, user=user, arm=arm,
        variation_id=str(variant["variation_id"]), meta=meta or {},
    )


def _label_text(label: str) -> str:
    return {
        "pathogenic": "Pathogenic / Likely pathogenic",
        "benign": "Benign / Likely benign",
    }.get(label, label)
