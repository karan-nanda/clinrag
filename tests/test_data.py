import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.data import clinvar, splits


def test_significance_normalisation():
    sig = pd.Series([
        "Pathogenic",
        "Likely pathogenic",
        "Pathogenic/Likely pathogenic",
        "Benign",
        "Benign/Likely benign",
        "Uncertain significance",
        "Conflicting classifications of pathogenicity",
        "drug response",
        "risk factor",
    ])
    got = list(clinvar.normalise_significance(sig))
    assert got == [
        "pathogenic", "pathogenic", "pathogenic",
        "benign", "benign",
        "vus",
        "other",   # conflicting must NOT become pathogenic
        "other", "other",
    ]


def test_review_stars_unknown_status_is_negative():
    s = pd.Series([
        "criteria provided, multiple submitters, no conflicts",
        "criteria provided, single submitter",
        "reviewed by expert panel",
        "some new wording ClinVar invented",
    ])
    assert list(clinvar.review_stars(s)) == [2, 1, 3, -1]


def _toy(n_genes=30, per_gene=6):
    rows = []
    for g in range(n_genes):
        for i in range(per_gene):
            rows.append({
                "gene": f"GENE{g}",
                "label": "pathogenic" if i % 2 == 0 else "benign",
            })
    return pd.DataFrame(rows)


def test_gene_level_split_keeps_genes_whole():
    df = _toy()
    df["split"] = splits.gene_level_split(df, {"train": 0.6, "dev": 0.15, "test": 0.25}, seed=1)
    per_gene = df.groupby("gene")["split"].nunique()
    assert (per_gene == 1).all()
    assert set(df["split"]) == {"train", "dev", "test"}


def test_gene_level_split_respects_fractions():
    df = _toy(n_genes=100, per_gene=4)
    df["split"] = splits.gene_level_split(df, {"train": 0.6, "dev": 0.15, "test": 0.25}, seed=0)
    frac = df["split"].value_counts(normalize=True)
    assert abs(frac["train"] - 0.6) < 0.05
    assert abs(frac["test"] - 0.25) < 0.05


def test_fractions_must_sum_to_one():
    with pytest.raises(ValueError):
        splits.gene_level_split(_toy(), {"train": 0.6, "test": 0.2}, seed=0)


def test_balance_is_equal_and_capped():
    df = pd.DataFrame({
        "gene": ["BRCA1"] * 200 + [f"G{i}" for i in range(200)],
        "label": ["pathogenic"] * 200 + ["benign"] * 200,
    })
    # BRCA1 alone could supply every pathogenic row; the cap must stop it.
    with pytest.raises(ValueError, match="only"):
        clinvar.balance(df, target_n=100, max_per_gene=5, seed=0)


def test_temporal_holdout_missing_dates_are_false():
    df = pd.DataFrame({"last_evaluated": pd.to_datetime(["2020-01-01", "2025-06-01", None])})
    assert list(splits.temporal_holdout(df, "2024-01-01")) == [False, True, False]


def test_gene_level_split_balances_prevalence():
    """Genes that are internally all-one-label must not skew fold prevalence."""
    rows = []
    for g in range(60):
        label = "pathogenic" if g % 2 == 0 else "benign"
        for _ in range(g % 5 + 1):          # uneven gene sizes
            rows.append({"gene": f"GENE{g}", "label": label})
    df = pd.DataFrame(rows)
    df["split"] = splits.gene_level_split(df, {"train": 0.6, "dev": 0.15, "test": 0.25}, seed=3)
    rates = df.groupby("split")["label"].apply(lambda s: (s == "pathogenic").mean())
    assert rates.max() - rates.min() <= 0.05
    assert (df.groupby("gene")["split"].nunique() == 1).all()


def test_gene_level_split_raises_on_unbalanced_folds():
    """One dominant single-label gene cannot be balanced away; fail loudly, not silently."""
    df = pd.DataFrame({
        "gene": ["BIG"] * 300 + [f"G{i}" for i in range(20)],
        "label": ["pathogenic"] * 300 + ["benign"] * 20,
    })
    with pytest.raises(ValueError, match="unbalanced folds"):
        splits.gene_level_split(df, {"train": 0.5, "test": 0.5}, seed=0)


def test_rsid_is_canonicalised():
    df = pd.DataFrame({
        "rsid": ["80357906", "-1", None, "1952025232"],
        "gene": ["A", "B", "C", "D"],
    })
    rs = df["rsid"].fillna("").str.strip()
    got = ("rs" + rs).where(rs.str.fullmatch(r"\d+") & rs.ne("-1"), pd.NA)
    assert list(got.fillna("NA")) == ["rs80357906", "NA", "NA", "rs1952025232"]
