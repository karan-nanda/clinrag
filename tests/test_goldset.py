import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import goldset


def test_extract_pmids_handles_clinvar_citation_formats():
    text = (
        "Observed in affected individuals (PMID: 12345678). "
        "Functional data reported (PMID 23456789) and in PMID:34567890. "
        "Multiple reports (PMIDs: 11111111, 22222222)."
    )
    got = goldset.extract_pmids(text)
    assert {"12345678", "23456789", "34567890", "11111111", "22222222"} <= got


def test_extract_pmids_ignores_bare_numbers():
    assert goldset.extract_pmids("gnomAD frequency 0.003% in 141456 alleles") == set()


def test_build_skips_variants_citing_nothing():
    df = pd.DataFrame(
        {
            "VariationID": ["1", "1", "2"],
            "Description": [
                "Reported in patients (PMID: 12345678).",
                "Further cases (PMID: 23456789).",
                "This variant is present in population databases at high frequency.",
            ],
        }
    )
    gold = goldset.build(df)
    assert gold == {"1": {"12345678", "23456789"}}
    assert "2" not in gold, "a variant citing nothing must be absent, not present-and-empty"


def test_score_separates_corpus_misses_from_ranking_misses():
    # gold has 2 papers; only one is in the pool, and it is ranked second.
    triples = [({"A", "B"}, {"A", "X"}, [{"X"}, {"A"}])]
    sc = goldset.score(triples, ks=(1, 2))
    assert sc.pool_recall == 0.5, "one of two cited papers is reachable"
    assert sc.recall_at_k[1] == 0.0, "reachable paper not in top-1"
    assert sc.recall_at_k[2] == 1.0, "reachable paper found by top-2"


def test_ranking_is_not_penalised_for_unreachable_papers():
    """A paper absent from the pool is a corpus failure; charging it to the ranker too
    would double-count and make a corpus problem look like a retriever problem."""
    both_reachable = [({"A"}, {"A"}, [{"A"}])]
    one_unreachable = [({"A", "B"}, {"A"}, [{"A"}])]
    assert goldset.score(both_reachable, ks=(1,)).recall_at_k[1] == 1.0
    assert goldset.score(one_unreachable, ks=(1,)).recall_at_k[1] == 1.0
    assert goldset.score(one_unreachable, ks=(1,)).pool_recall == 0.5


def test_score_handles_empty_gold():
    sc = goldset.score([(set(), {"A"}, [{"A"}])], ks=(1,))
    assert sc.variants == 0
