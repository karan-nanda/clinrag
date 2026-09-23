import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import pubtator as pt
from clinrag.retrieval import sanitize as S

# Shaped like the live PubTator3 export response, which nests under "PubTator3" and puts the
# normalized gene symbol in infons.name (the surface text may be "BRCA1/2").
DOC = {
    "PubTator3": [
        {
            "id": "111",
            "pmid": "111",
            "date": "2021-05-01",
            "journal": "J Test",
            "passages": [
                {
                    "infons": {"type": "title", "year": "2021"},
                    "text": "A study of BRCA1 c.1067A>G in ovarian cancer patients worldwide.",
                    "annotations": [
                        {
                            "text": "BRCA1/2",
                            "infons": {"type": "Gene", "name": "BRCA1", "identifier": "672;675"},
                        },
                        {
                            "text": "c.1067 A>G",
                            "infons": {
                                "type": "Variant",
                                "rsids": ["rs1799950"],
                                "hgvs": "c.1067A>G",
                            },
                        },
                    ],
                },
                {"infons": {"type": "abstract_title_1"}, "text": "Methods", "annotations": []},
                {
                    "infons": {"type": "ref"},
                    "text": "Smith J et al. Nature 2019;123:45-67. A paper about something else entirely.",
                    "annotations": [],
                },
                {
                    "infons": {"type": "abstract"},
                    "text": "We sequenced a large cohort and found recurrent alterations in this gene.",
                    "annotations": [
                        {"text": "BRCA1", "infons": {"type": "Gene", "name": "BRCA1",
                                                      "identifier": "672"}}
                    ],
                },
            ],
        }
    ]
}


def _parse():
    """Parse DOC the way `fetch` would, without touching the network."""
    return [p for d in pt._iter_docs(json.dumps(DOC)) for p in pt._parse_doc(d)]


def test_iter_docs_unwraps_pubtator3_key():
    docs = list(pt._iter_docs(json.dumps(DOC)))
    assert len(docs) == 1 and docs[0]["pmid"] == "111"


def test_iter_docs_handles_documents_key_and_bare_list():
    assert len(list(pt._iter_docs(json.dumps({"documents": [{"id": "1"}]})))) == 1
    assert len(list(pt._iter_docs(json.dumps([{"id": "1"}, {"id": "2"}])))) == 2


def test_gene_symbol_comes_from_normalized_name_not_surface_text():
    ps = _parse()
    title = next(p for p in ps if p.section == "title")
    assert title.genes == ["BRCA1"]          # not "BRCA1/2"
    assert set(title.gene_ids) == {"672", "675"}
    assert title.rsids == ["rs1799950"]
    assert title.hgvs == ["c.1067A>G"]


def test_passage_ids_are_unique():
    ps = _parse()
    assert len({p.passage_id for p in ps}) == len(ps)


def test_prepare_pool_drops_references_and_headings():
    ps = _parse()
    pool = pt.prepare_pool(ps, "BRCA1", "rs1799950")
    sections = {p.section for p in pool}
    assert "ref" not in sections, "bibliography entries must never enter the evidence pool"
    assert not any(s.startswith("abstract_title") for s in sections)
    assert sections <= pt.ABSTRACT_SECTIONS


def test_evidence_level_distinguishes_variant_from_gene():
    ps = _parse()
    title = next(p for p in ps if p.section == "title")
    abstract = next(p for p in ps if p.section == "abstract")
    assert title.evidence_level("BRCA1", "rs1799950") == "variant"
    assert abstract.evidence_level("BRCA1", "rs1799950") == "gene"
    assert abstract.evidence_level("TP53", "rs999") == "other"


def test_evidence_level_matches_rsid_regardless_of_prefix():
    ps = _parse()
    title = next(p for p in ps if p.section == "title")
    assert title.evidence_level("BRCA1", "1799950") == "variant"


# ---------------------------------------------------------------- sanitation


def test_sanitize_removes_verdict_and_acmg_sentences():
    text = (
        "This variant is present in population databases (rs80357906, gnomAD 0.003%). "
        "Experimental studies have shown that this variant disrupts protein function. "
        "In summary, the available evidence indicates that the variant is pathogenic. "
        "ACMG criteria applied: PVS1, PM2, PP3."
    )
    rep = S.SanitationReport()
    out = S.sanitize(text, rep)
    assert "pathogenic" not in out.lower()
    assert "PVS1" not in out
    assert "population databases" in out
    assert "disrupts protein function" in out
    assert rep.sentences_in == 4 and rep.sentences_out == 2


def test_sanitize_can_empty_a_pure_verdict_comment():
    assert S.sanitize("This variant is classified as likely benign.").strip() == ""


@pytest.mark.parametrize("code", ["PVS1", "PM2", "BA1", "BS3", "BP7", "PP5", "PS4"])
def test_acmg_codes_are_stripped(code):
    assert S.sanitize(f"Evidence codes applied were {code} in this case.").strip() == ""


def test_sanitize_keeps_neutral_evidence_sentences():
    text = "This missense change has been observed in 12 individuals in a research cohort."
    assert S.sanitize(text).strip() == text


# ---------------------------------------------------------------- dedup


def test_dedupe_collapses_identical_and_keeps_distinct():
    a = "This variant is present in population databases at low frequency in all cohorts."
    b = "A completely unrelated sentence about yeast two hybrid functional assay results."
    assert S.dedupe([a, a, b]) == [0, 2]


def test_dedupe_is_exact_for_small_pools():
    a = "one two three four five six seven eight nine ten"
    assert S._jaccard(S._shingles(a), S._shingles(a)) == 1.0
    assert S.dedupe([a, a]) == [0]


def test_minhash_approximates_jaccard_on_realistic_text():
    a = (
        "This sequence change replaces arginine with cysteine at codon 1443 of the protein. "
        "The residue is highly conserved across species and the change is non conservative. "
        "This variant is present in population databases at very low overall frequency."
    )
    b = a.replace("codon 1443", "codon 1699").replace("cysteine", "histidine")
    exact = S._jaccard(S._shingles(a), S._shingles(b))
    approx = S._similarity(S._signature(S._shingles(a)), S._signature(S._shingles(b)))
    assert abs(exact - approx) < 0.15
