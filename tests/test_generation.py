import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.generation import backends, distractors, prompts

VARIANT = {
    "variation_id": "12345",
    "gene": "BRCA1",
    "Name": "NM_007294.4(BRCA1):c.68_69del (p.Glu23fs)",
    "rsid": "rs80357906",
    "chrom": "17",
    "pos": 43124096,
    "ref": "A",
    "alt": "G",
    "phenotypes": "Hereditary breast ovarian cancer syndrome|not provided",
    "label": "pathogenic",
    "split": "dev",
}

PASSAGES = [
    {"doc_id": "d1", "source": "clinvar_comment", "text": "Observed in affected individuals.",
     "level": "variant", "year": 2021},
    {"doc_id": "d2", "source": "pubtator", "text": "The gene is implicated in DNA repair.",
     "level": "gene", "year": 2019},
]


# ------------------------------------------------------------------ prompts


def test_all_arms_share_identical_structure_outside_evidence():
    """The core experimental requirement: arms differ ONLY in the evidence block."""
    built = {
        "grounded": prompts.build(VARIANT, "grounded", PASSAGES),
        "ungrounded": prompts.build(VARIANT, "ungrounded", []),
        "distractor_same_gene": prompts.build(VARIANT, "distractor_same_gene", PASSAGES),
        "distractor_other_gene": prompts.build(VARIANT, "distractor_other_gene", PASSAGES),
    }

    def shell(text):
        head, _, rest = text.partition("\nEVIDENCE\n")
        _, _, tail = rest.partition("\nTASK\n")
        return head + tail

    shells = {shell(p.user) for p in built.values()}
    assert len(shells) == 1, "arms must not differ outside the EVIDENCE block"
    assert len({p.system for p in built.values()}) == 1


def test_ungrounded_keeps_the_evidence_header():
    """Dropping the section would change prompt shape, not just content."""
    p = prompts.build(VARIANT, "ungrounded", [])
    assert "EVIDENCE" in p.user
    assert prompts.NO_EVIDENCE in p.user


def test_evidence_level_is_never_shown_to_the_model():
    """Revealing which passages are variant- vs gene-level would hand the model the
    distinction the conflation taxonomy category is meant to detect."""
    p = prompts.build(VARIANT, "grounded", PASSAGES)
    assert "variant-level" not in p.user
    evidence_block = p.user.split("EVIDENCE")[1].split("TASK")[0]
    assert "level" not in evidence_block.lower()


def test_task_text_does_not_instruct_the_model_to_stick_to_evidence():
    """Such an instruction would suppress unsupported claims directly - the very quantity
    being measured."""
    low = prompts.TASK.lower()
    for banned in ("only the evidence", "only use", "do not speculate", "based solely"):
        assert banned not in low


def test_ungrounded_rejects_passages_and_grounded_requires_them():
    with pytest.raises(ValueError):
        prompts.build(VARIANT, "ungrounded", PASSAGES)
    with pytest.raises(ValueError):
        prompts.build(VARIANT, "grounded", [])


def test_unknown_arm_rejected():
    with pytest.raises(ValueError, match="unknown arm"):
        prompts.build(VARIANT, "not_an_arm", PASSAGES)


def test_evidence_is_numbered_for_traceability():
    block = prompts.format_evidence(PASSAGES)
    assert block.startswith("[1]") and "[2]" in block


def test_classification_is_rendered_not_raw_label():
    p = prompts.build(VARIANT, "ungrounded", [])
    assert "Pathogenic / Likely pathogenic" in p.user


# ------------------------------------------------------------------ distractors


def _pair_set():
    out = []
    for g in range(4):
        for i in range(2):
            for label in ("pathogenic", "benign"):
                out.append({
                    "variation_id": f"{g}{i}{label[0]}", "gene": f"GENE{g}",
                    "label": label, "split": "dev",
                })
    return out


def test_distractors_are_label_matched_and_different_variants():
    vs = _pair_set()
    by_id = {str(v["variation_id"]): v for v in vs}
    assign = distractors.assign(vs, seed=1)
    for vid, picks in assign.items():
        me = by_id[vid]
        for key in ("same_gene", "other_gene"):
            other = by_id[picks[key]]
            assert other["label"] == me["label"], "distractor must match label"
            assert str(other["variation_id"]) != vid


def test_same_gene_distractor_shares_the_gene_and_other_does_not():
    vs = _pair_set()
    by_id = {str(v["variation_id"]): v for v in vs}
    assign = distractors.assign(vs, seed=1)
    for vid, picks in assign.items():
        assert by_id[picks["same_gene"]]["gene"] == by_id[vid]["gene"]
        assert by_id[picks["other_gene"]]["gene"] != by_id[vid]["gene"]


def test_distractors_never_cross_a_split_boundary():
    vs = _pair_set() + [
        dict(v, variation_id=f"t{v['variation_id']}", split="test") for v in _pair_set()
    ]
    by_id = {str(v["variation_id"]): v for v in vs}
    assign = distractors.assign(vs, seed=0)
    for vid, picks in assign.items():
        for other in picks.values():
            assert by_id[other]["split"] == by_id[vid]["split"]


def test_assignment_is_deterministic():
    vs = _pair_set()
    assert distractors.assign(vs, seed=7) == distractors.assign(vs, seed=7)


def test_missing_same_gene_partner_is_omitted_not_faked():
    """A lone variant must yield no same_gene entry rather than a different gene's."""
    vs = [
        {"variation_id": "1", "gene": "A", "label": "benign", "split": "dev"},
        {"variation_id": "2", "gene": "B", "label": "benign", "split": "dev"},
    ]
    assign = distractors.assign(vs, seed=0)
    assert "same_gene" not in assign["1"]
    assert assign["1"]["other_gene"] == "2"


# ------------------------------------------------------------------ backends


def test_custom_id_round_trips():
    cid = backends.make_custom_id("12345", "distractor_same_gene", backends.OPUS, replicate=2)
    got = backends.parse_custom_id(cid)
    assert got["variation_id"] == "12345"
    assert got["arm"] == "distractor_same_gene"
    assert got["replicate"] == 2


def test_model_ids_carry_no_date_suffix():
    for mid in (backends.OPUS, backends.SONNET, backends.HAIKU):
        assert not any(part.isdigit() and len(part) == 8 for part in mid.split("-"))


def test_claude_request_params_omit_temperature():
    """Current models reject temperature/top_p/top_k with a 400."""
    gen = backends.ClaudeGenerator.__new__(backends.ClaudeGenerator)
    gen.model, gen.effort, gen.max_tokens = backends.OPUS, "medium", 2000
    params = gen._request_params("sys", "user")
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in params
    assert params["output_config"] == {"effort": "medium"}


def test_echo_backend_is_obviously_synthetic():
    g = backends.EchoGenerator().generate(prompts.SYSTEM, "Gene: BRCA1\n")
    assert "SYNTHETIC STUB" in g.text
    assert "BRCA1" in g.text


# ------------------------------------------- distractor usability metadata


def _p(text):
    return [{"text": text, "source": "clinvar_comment", "level": "variant"}]


def test_identical_distractor_is_flagged():
    """Two variants in one gene can retrieve the same passages; that is not a distractor."""
    same = _p("Identical evidence text.")
    m = distractors.describe_distractor(same, list(same))
    assert m["identical_to_grounded"] is True
    m2 = distractors.describe_distractor(same, _p("Different evidence text entirely."))
    assert m2["identical_to_grounded"] is False


def test_names_donor_requires_the_donor_identifier():
    donor = {"rsid": "rs12345678", "Name": "NM_1.2(GENE):c.100A>G (p.Lys34Arg)"}
    keys = distractors.donor_keys(donor)
    assert "rs12345678" in keys and "p.Lys34Arg" in keys and "c.100A>G" in keys

    hit = distractors.describe_distractor([], _p("Reported as p.Lys34Arg in two families."), keys)
    assert hit["names_donor"] is True

    miss = distractors.describe_distractor([], _p("The gene is involved in DNA repair."), keys)
    assert miss["names_donor"] is False
    assert miss["names_a_variant"] is False


def test_names_a_variant_detects_any_variant_mention():
    """If evidence names no variant at all, the model cannot detect a mismatch and the arm
    is not testing conflation."""
    for text, expected in [
        ("Observed as rs80357906 in a cohort.", True),
        ("The change c.1067A>G was seen.", True),
        ("Reported as p.Gln356Arg previously.", True),
        ("This gene is associated with cancer risk.", False),
    ]:
        assert distractors.describe_distractor([], _p(text))["names_a_variant"] is expected


def test_donor_keys_ignores_missing_rsid():
    assert distractors.donor_keys({"rsid": None, "Name": ""}) == []
    assert distractors.donor_keys({"rsid": "nan", "Name": ""}) == []


def test_prompt_carries_meta_through():
    p = prompts.build(VARIANT, "distractor_same_gene", PASSAGES, meta={"donor_id": "999"})
    assert p.meta["donor_id"] == "999"
    assert p.as_dict()["meta"]["donor_id"] == "999"
