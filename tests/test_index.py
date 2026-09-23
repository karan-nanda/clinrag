import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval.index import (
    BM25Retriever,
    Evidence,
    apply_budget,
    budget_report,
    recall_at_k,
    tokenize,
    variant_query,
)


def ev(doc_id, text, level="gene", source="pubtator"):
    return Evidence(doc_id=doc_id, source=source, text=text, level=level)


def test_tokenize_keeps_rsids_and_hgvs_intact():
    toks = tokenize("BRCA1 c.1067A>G rs80357906 p.Gln356Arg")
    assert "rs80357906" in toks
    assert "c.1067a>g" in toks, "HGVS must survive tokenization as one token"
    assert "p.gln356arg" in toks


def test_variant_query_skips_missing_fields():
    assert variant_query("BRCA1", rsid=None, hgvs=None) == "BRCA1"
    assert variant_query("BRCA1", rsid="rs1") == "BRCA1 rs1"


def test_bm25_ranks_matching_text_first():
    # Needs a corpus large enough for IDF to mean something; see the degeneracy test below.
    pool = [ev(f"n{i}", f"unrelated text about yeast metabolism sample {i}") for i in range(10)]
    pool.append(ev("b", "BRCA1 rs80357906 was observed in a hereditary breast cancer cohort"))
    top = BM25Retriever(pool).search("BRCA1 rs80357906", k=1)
    assert top[0][0].doc_id == "b"


def test_bm25_idf_collapses_on_a_degenerate_corpus():
    """Documents the failure mode the tie-break exists to contain.

    When every query term appears in half the corpus, BM25Okapi's IDF is exactly 0 and all
    documents score 0. Our pools are gene-filtered, so the gene symbol trends this way and
    contributes almost nothing to ranking -- the variant-specific tokens do the work.
    """
    pool = [
        ev("a", "unrelated text about yeast metabolism and cell walls"),
        ev("b", "BRCA1 rs80357906 in a hereditary breast cancer cohort"),
    ]
    scored = BM25Retriever(pool).search("BRCA1 rs80357906", k=2)
    assert all(s == 0.0 for _, s in scored), "expected degenerate all-zero scoring"


def test_ties_are_broken_deterministically_variant_level_first():
    pool = [
        ev("z_gene", "identical filler text", level="gene"),
        ev("a_variant", "identical filler text", level="variant"),
    ]
    once = [e.doc_id for e, _ in BM25Retriever(pool).search("nomatchingterm", k=2)]
    twice = [e.doc_id for e, _ in BM25Retriever(pool).search("nomatchingterm", k=2)]
    assert once == twice, "ranking must be reproducible for temperature-0 runs"
    assert once[0] == "a_variant"


def test_bm25_on_empty_pool_does_not_raise():
    assert BM25Retriever([]).search("anything", k=5) == []


# ------------------------------------------------------- budget: reserve and cap


def _ranked(evs):
    """Simulate a ranking where variant-level evidence sits at the bottom, which is what
    BM25 actually does: a comment says 'this variant', a gene abstract repeats the symbol."""
    return [(e, float(-i)) for i, e in enumerate(evs)]


def test_budget_reserves_a_slot_for_buried_variant_level_evidence():
    evs = [ev(f"g{i}", "gene level abstract text " * 5) for i in range(20)]
    evs.append(ev("c1", "sanitized submitter comment about this exact variant",
                  level="variant", source="clinvar_comment"))
    out = apply_budget(_ranked(evs), k=5, max_chars=100_000, variant_level_slots=1)
    assert out[0].doc_id == "c1", "the reserved slot must be filled first"
    assert len(out) == 5


def test_budget_caps_variant_level_so_labels_stay_comparable():
    # A pathogenic variant with 4 comments must not receive more than a benign one with 1.
    evs = [ev(f"c{i}", f"comment {i} text", level="variant", source="clinvar_comment")
           for i in range(4)]
    evs += [ev(f"g{i}", f"gene abstract {i}") for i in range(6)]
    out = apply_budget(_ranked(evs), k=5, max_chars=100_000, variant_level_slots=1)
    assert sum(1 for e in out if e.level == "variant") == 1


def test_budget_respects_character_cap_and_skips_rather_than_truncates():
    evs = [ev("big", "x" * 5000), ev("small", "y" * 100)]
    out = apply_budget(_ranked(evs), k=5, max_chars=1000, variant_level_slots=0)
    assert [e.doc_id for e in out] == ["small"]
    assert all(len(e.text) == len(e.text.strip()) for e in out)
    assert "x" * 5000 not in [e.text for e in out]


def test_budget_zero_slots_disables_reserve_and_cap():
    evs = [ev("c1", "comment", level="variant"), ev("c2", "comment2", level="variant")]
    out = apply_budget(_ranked(evs), k=5, max_chars=10_000, variant_level_slots=0)
    assert len(out) == 2


# ------------------------------------------------------- reporting


def test_budget_report_flags_a_surviving_length_cue():
    delivered = {
        "p1": [ev("a", "x" * 3000)],
        "b1": [ev("b", "y" * 500)],
    }
    rep = budget_report(delivered, {"p1": "pathogenic", "b1": "benign"})
    assert rep["char_ratio"] == 6.0
    assert rep["pathogenic"]["mean_chars"] == 3000


def test_budget_report_ratio_near_one_when_balanced():
    delivered = {"p1": [ev("a", "x" * 1000)], "b1": [ev("b", "y" * 1010)]}
    rep = budget_report(delivered, {"p1": "pathogenic", "b1": "benign"})
    assert rep["char_ratio"] == 1.01


def test_recall_at_k():
    retrieved = [ev("a", "t"), ev("b", "t"), ev("c", "t")]
    assert recall_at_k(retrieved, {"a", "b"}) == 1.0
    assert recall_at_k(retrieved, {"a", "z"}) == 0.5
    assert recall_at_k(retrieved, {"a", "b"}, k=1) == 0.5
