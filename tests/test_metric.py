import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.metric import aggregate, claims, verify


def mk_claim(text, cid="v1:grounded:0", scope=None, ctype=None, arm="grounded"):
    return claims.Claim(
        claim_id=cid, variation_id="v1", arm=arm, model="m", text=text,
        scope=scope or claims.classify_scope(text),
        claim_type=ctype or claims.classify_type(text),
    )


# ----------------------------------------------------------------- decomposition


def test_scope_detects_variant_specific_language():
    for text in [
        "This variant is absent from population databases.",
        "The substitution alters a conserved residue.",
        "Observed as rs80357906 in two families.",
        "The change c.1067A>G was reported.",
    ]:
        assert claims.classify_scope(text) == "variant", text


def test_scope_separates_gene_level_from_variant_level():
    """The conflation category depends entirely on this distinction."""
    assert claims.classify_scope("BRCA1 is involved in homologous recombination.") == "gene"
    assert claims.classify_scope("This variant impairs homologous recombination.") == "variant"


@pytest.mark.parametrize("text,expected", [
    ("Absent from gnomAD and 1000 Genomes.", "population_frequency"),
    ("Functional studies show loss of function.", "functional"),
    ("Reported in three affected individuals.", "case_observation"),
    ("The condition shows autosomal recessive inheritance.", "inheritance"),
    ("Described previously (PMID: 12345678).", "citation"),
    ("The residue is highly conserved across species.", "conservation"),
    ("Predicted to trigger nonsense-mediated decay.", "mechanism"),
])
def test_claim_types_map_to_taxonomy_categories(text, expected):
    assert claims.classify_type(text) == expected


def test_citation_type_wins_over_other_matches():
    """A fabricated citation is its own taxonomy category regardless of what else the
    sentence asserts."""
    text = "Functional studies showed loss of function (PMID: 12345678)."
    assert claims.classify_type(text) == "citation"


def test_sentence_decomposer_splits_and_indexes():
    text = (
        "This variant is absent from gnomAD. "
        "Functional assays show reduced activity. "
        "Short."
    )
    got = claims.SentenceDecomposer().decompose(text, "v1", "grounded", "m")
    assert len(got) == 2, "fragments below the minimum length are dropped"
    assert got[0].source_sentence == 0 and got[1].source_sentence == 1
    assert len({c.claim_id for c in got}) == 2


def test_parse_claims_falls_back_when_model_returns_bad_labels():
    payload = {"claims": [
        {"text": "This variant is absent from gnomAD.", "scope": "nonsense",
         "claim_type": "bogus", "source_sentence": 0},
        {"text": "tiny", "scope": "variant", "claim_type": "other", "source_sentence": 1},
    ]}
    got = claims.parse_claims(payload, "v1", "grounded", "m")
    assert len(got) == 1, "too-short claims are dropped"
    assert got[0].scope == "variant"
    assert got[0].claim_type == "population_frequency"


# ----------------------------------------------------------------- verification

POOL = [
    {"doc_id": "gene1", "text": "BRCA1 participates in homologous recombination repair.",
     "level": "gene"},
    {"doc_id": "var1", "text": "The variant rs80357906 was observed in affected individuals.",
     "level": "variant"},
]


def test_conflation_flags_variant_claim_supported_only_by_gene_evidence():
    """The headline detector: variant-specific authority resting on gene-level evidence."""
    c = mk_claim("This variant impairs homologous recombination repair.", scope="variant")
    v = verify.Verdict(claim_id=c.claim_id, label="supported", supporting_doc_ids=["gene1"])
    assert verify.detect_conflation(c, v, POOL) is True


def test_conflation_not_flagged_when_variant_evidence_supports():
    c = mk_claim("This variant was observed in affected individuals.", scope="variant")
    v = verify.Verdict(claim_id=c.claim_id, label="supported",
                       supporting_doc_ids=["gene1", "var1"])
    assert verify.detect_conflation(c, v, POOL) is False


def test_conflation_not_flagged_for_gene_scoped_claims():
    """A gene-level claim supported by gene-level evidence is simply correct."""
    c = mk_claim("BRCA1 participates in homologous recombination repair.", scope="gene")
    v = verify.Verdict(claim_id=c.claim_id, label="supported", supporting_doc_ids=["gene1"])
    assert verify.detect_conflation(c, v, POOL) is False


def test_conflation_requires_support_so_it_is_not_double_counted():
    c = mk_claim("This variant does something.", scope="variant")
    v = verify.Verdict(claim_id=c.claim_id, label="unsupported")
    assert verify.detect_conflation(c, v, POOL) is False


def test_judge_claiming_support_without_citing_passages_is_downgraded():
    """An unevidenced assertion of support is not support."""
    c = mk_claim("This variant is pathogenic in every carrier.")
    v = verify.verdict_from_payload(
        {"label": "supported", "supporting_passages": [], "contradicting_passages": [],
         "rationale": "trust me"},
        c, POOL, judge="llm:test",
    )
    assert v.label == "unsupported"


def test_unsupported_verdict_carries_no_passage_ids():
    c = mk_claim("This variant is absent from gnomAD.")
    v = verify.verdict_from_payload(
        {"label": "unsupported", "supporting_passages": [0], "contradicting_passages": [1],
         "rationale": "x"},
        c, POOL, judge="llm:test",
    )
    assert v.supporting_doc_ids == [] and v.contradicting_doc_ids == []


def test_out_of_range_passage_indices_are_discarded():
    c = mk_claim("This variant was observed in affected individuals.")
    v = verify.verdict_from_payload(
        {"label": "supported", "supporting_passages": [1, 99, -3],
         "contradicting_passages": [], "rationale": "x"},
        c, POOL, judge="llm:test",
    )
    assert v.supporting_doc_ids == ["var1"]


def test_self_preference_confound_is_flagged():
    c = mk_claim("This variant was observed in affected individuals.")
    same = verify.verdict_from_payload(
        {"label": "supported", "supporting_passages": [1], "contradicting_passages": [],
         "rationale": "x"},
        c, POOL, judge="llm:claude-opus-5", generator_model="claude-sonnet-5",
    )
    assert same.same_family_as_generator is True
    diff = verify.verdict_from_payload(
        {"label": "supported", "supporting_passages": [1], "contradicting_passages": [],
         "rationale": "x"},
        c, POOL, judge="llm:claude-opus-5", generator_model="llama-3-70b",
    )
    assert diff.same_family_as_generator is False


def test_lexical_verifier_cannot_detect_contradiction():
    """Documents the baseline's known ceiling - this is why a real judge is needed."""
    c = mk_claim("The variant was observed in affected individuals.")
    v = verify.LexicalVerifier().verify(c, POOL)
    assert v.label in ("supported", "unsupported")
    assert v.contradicting_doc_ids == []


# ------------------------------------------------------------------ aggregation


def _rates(arm, values, model="m"):
    """One ExplanationRates per variant with a given metric value."""
    return [
        aggregate.ExplanationRates(
            variation_id=f"v{i}", arm=arm, model=model, n_claims=10, unsupported=val,
        )
        for i, val in enumerate(values)
    ]


def test_explanation_rates_counts_and_normalises():
    cs = [
        mk_claim("This variant is absent from gnomAD.", cid="c0"),
        mk_claim("Functional assays show reduced activity.", cid="c1"),
        mk_claim("Reported in three affected individuals.", cid="c2"),
        mk_claim("The gene is on chromosome 17.", cid="c3"),
    ]
    vs = [
        verify.Verdict(claim_id="c0", label="supported", supporting_doc_ids=["gene1"]),
        verify.Verdict(claim_id="c1", label="unsupported"),
        verify.Verdict(claim_id="c2", label="contradicted", contradicting_doc_ids=["var1"]),
        verify.Verdict(claim_id="c3", label="supported", supporting_doc_ids=["gene1"],
                       conflation=True),
    ]
    got = aggregate.explanation_rates(cs, vs)
    assert len(got) == 1
    r = got[0]
    assert r.n_claims == 4
    assert r.supported == 0.5 and r.unsupported == 0.25 and r.contradicted == 0.25
    assert r.conflation == 0.25


def test_explanation_with_no_claims_is_kept_not_dropped():
    """A model that says nothing checkable is a result; dropping it would bias the arm."""
    got = aggregate.explanation_rates([], [])
    assert got == []
    rows = aggregate.explanation_rates(
        [mk_claim("This variant is absent from gnomAD.", cid="c0")],
        [],  # no verdict recorded
    )
    assert rows[0].n_claims == 1 and rows[0].supported == 0.0


def test_bootstrap_ci_brackets_a_real_difference():
    a = _rates("ungrounded", [0.8] * 40)
    b = _rates("grounded", [0.2] * 40)
    res = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=500, seed=1)
    assert res.n_pairs == 40
    assert res.diff == pytest.approx(0.6, abs=1e-9)
    assert not res.crosses_zero


def test_bootstrap_ci_covers_zero_when_arms_are_equal():
    a = _rates("ungrounded", [0.5, 0.4, 0.6, 0.5, 0.45, 0.55] * 5)
    b = _rates("grounded", [0.5, 0.4, 0.6, 0.5, 0.45, 0.55] * 5)
    res = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=500, seed=2)
    assert res.crosses_zero


def test_bootstrap_pairs_only_shared_variants():
    """Distractor arms drop variants; the comparison must stay paired."""
    a = _rates("ungrounded", [0.5] * 10)
    b = _rates("grounded", [0.2] * 10)[:6]
    res = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=200, seed=3)
    assert res.n_pairs == 6


def test_bootstrap_is_deterministic_given_a_seed():
    a, b = _rates("ungrounded", [0.7] * 20), _rates("grounded", [0.3] * 20)
    r1 = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=300, seed=9)
    r2 = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=300, seed=9)
    assert (r1.ci_low, r1.ci_high) == (r2.ci_low, r2.ci_high)


def test_bootstrap_rejects_unknown_metric_and_disjoint_arms():
    a, b = _rates("ungrounded", [0.5] * 5), _rates("grounded", [0.5] * 5)
    with pytest.raises(ValueError, match="unknown metric"):
        aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", metric="nope")
    with pytest.raises(ValueError, match="no variants"):
        aggregate.bootstrap_diff(a, "ungrounded", "grounded")


def test_paired_bootstrap_is_tighter_than_ignoring_pairing():
    """The point of pairing: between-variant variance is large and must not swamp the arm
    effect. A constant per-variant offset should not widen the interval."""
    offsets = [i / 50 for i in range(40)]
    a = _rates("ungrounded", [0.5 + o for o in offsets])
    b = _rates("grounded", [0.3 + o for o in offsets])
    res = aggregate.bootstrap_diff(a + b, "ungrounded", "grounded", n_boot=800, seed=4)
    assert res.diff == pytest.approx(0.2, abs=1e-9)
    assert (res.ci_high - res.ci_low) < 1e-6, "paired diffs are constant, so CI is a point"


def test_stratified_summary_splits_on_flag():
    rows = _rates("distractor_same_gene", [0.9, 0.9, 0.1, 0.1])
    strata = {
        ("v0", "distractor_same_gene"): True,
        ("v1", "distractor_same_gene"): True,
        ("v2", "distractor_same_gene"): False,
        ("v3", "distractor_same_gene"): False,
    }
    got = aggregate.stratified_summary(rows, strata, "distractor_same_gene")
    assert got["true"]["n"] == 2 and got["true"]["mean"] == pytest.approx(0.9)
    assert got["false"]["mean"] == pytest.approx(0.1)


def test_arm_summary_reports_per_arm_means():
    rows = _rates("grounded", [0.2, 0.4]) + _rates("ungrounded", [0.6, 0.8])
    got = aggregate.arm_summary(rows)
    assert got["grounded"]["unsupported"] == pytest.approx(0.3)
    assert got["ungrounded"]["unsupported"] == pytest.approx(0.7)
    assert got["grounded"]["n_explanations"] == 2


# ------------------------------------------------------------------ validation


def test_cohens_kappa_matches_known_values():
    from clinrag.metric import validation as val

    # Perfect agreement with variance present.
    assert val.cohens_kappa(["supported", "unsupported"] * 10,
                            ["supported", "unsupported"] * 10) == pytest.approx(1.0)
    # Complete disagreement on a balanced two-label set is strongly negative.
    assert val.cohens_kappa(["supported"] * 10 + ["unsupported"] * 10,
                            ["unsupported"] * 10 + ["supported"] * 10) == pytest.approx(-1.0)


def test_kappa_is_near_zero_for_chance_agreement():
    from clinrag.metric import validation as val

    a = ["supported", "unsupported"] * 25
    b = ["supported", "supported", "unsupported", "unsupported"] * 12 + ["supported", "supported"]
    k = val.cohens_kappa(a, b[: len(a)])
    assert abs(k) < 0.25, "coin-flip-like agreement must not look like signal"


def test_kappa_handles_constant_raters_without_nan():
    from clinrag.metric import validation as val

    assert val.cohens_kappa(["supported"] * 5, ["supported"] * 5) == 1.0
    assert val.cohens_kappa(["supported"] * 5, ["unsupported"] * 5) == 0.0


def test_per_label_scores_expose_a_judge_weak_on_contradiction():
    """A single accuracy number would hide this; per-label is why we split it out."""
    from clinrag.metric import validation as val

    gold = ["supported"] * 8 + ["contradicted"] * 2
    pred = ["supported"] * 10          # never predicts contradicted
    got = val.per_label_scores(gold, pred)
    assert got["supported"]["recall"] == pytest.approx(1.0)
    assert got["contradicted"]["recall"] == pytest.approx(0.0)
    assert got["contradicted"]["support"] == 2


def test_sampling_is_stratified_across_arms_and_labels():
    from clinrag.metric import validation as val

    cs, vs = [], []
    for arm in ("grounded", "ungrounded"):
        for label in ("supported", "unsupported", "contradicted"):
            for i in range(20):
                cid = f"{arm}:{label}:{i}"
                cs.append(claims.Claim(claim_id=cid, variation_id=f"v{i}", arm=arm,
                                       model="m", text="a claim about this variant here"))
                vs.append(verify.Verdict(claim_id=cid, label=label))

    got = val.sample_for_annotation(cs, vs, n=60, seed=1, oversample_same_family=0.0)
    by_id = {v.claim_id: v for v in vs}
    labels = {by_id[c.claim_id].label for c in got}
    assert labels == {"supported", "unsupported", "contradicted"}, (
        "rare labels must survive sampling; proportional sampling would lose contradictions"
    )
    assert {c.arm for c in got} == {"grounded", "ungrounded"}


def test_sampling_oversamples_self_preference_cases():
    from clinrag.metric import validation as val

    cs, vs = [], []
    for i in range(100):
        cid = f"c{i}"
        cs.append(claims.Claim(claim_id=cid, variation_id=f"v{i}", arm="grounded",
                               model="m", text="a claim about this variant here"))
        vs.append(verify.Verdict(claim_id=cid, label="supported",
                                 same_family_as_generator=i < 30))
    got = val.sample_for_annotation(cs, vs, n=20, seed=0, oversample_same_family=0.5)
    by_id = {v.claim_id: v for v in vs}
    n_sf = sum(1 for c in got if by_id[c.claim_id].same_family_as_generator)
    assert n_sf >= 10


def test_annotation_sheet_hides_the_auto_label_by_default(tmp_path):
    from clinrag.metric import validation as val

    cs = [claims.Claim(claim_id="c0", variation_id="v1", arm="grounded", model="m",
                       text="This variant is absent from gnomAD.")]
    p = val.write_annotation_sheet(tmp_path / "sheet.csv", cs, {"v1": POOL})
    text = p.read_text(encoding="utf-8")
    assert "auto_label" not in text, "showing the judge's answer would anchor the annotator"
    assert "This variant is absent from gnomAD." in text
    assert (tmp_path / "ANNOTATION_GUIDE.md").exists()


def test_round_trip_annotations_and_compare(tmp_path):
    from clinrag.metric import validation as val

    p = tmp_path / "done.csv"
    p.write_text(
        "claim_id,variation_id,claim_text,evidence,label,notes\n"
        "c0,v1,x,y,supported,\n"
        "c1,v1,x,y,UNSUPPORTED,\n"
        "c2,v1,x,y,,skipped\n"
        "c3,v1,x,y,bogus,\n",
        encoding="utf-8",
    )
    got = val.read_annotations(p)
    assert got == {"c0": "supported", "c1": "unsupported"}


def test_compare_scores_auto_against_each_annotator_and_consensus():
    from clinrag.metric import validation as val

    a = {"c0": "supported", "c1": "unsupported", "c2": "contradicted"}
    b = {"c0": "supported", "c1": "unsupported", "c2": "unsupported"}
    auto = {"c0": "supported", "c1": "unsupported", "c2": "unsupported"}
    got = val.compare(a, b, auto)
    assert got["n_shared"] == 3
    assert got["n_consensus"] == 2 and got["n_disagreed"] == 1
    assert got["auto_vs_consensus"].raw_agreement == pytest.approx(1.0)
    assert got["auto_vs_a"].raw_agreement < 1.0


def test_compare_requires_overlapping_ids():
    from clinrag.metric import validation as val

    with pytest.raises(ValueError, match="share no claim"):
        val.compare({"c0": "supported"}, {"c9": "supported"})
