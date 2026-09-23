"""Gene-level splitting.

A random variant-level split leaks: ClinVar's ascertainment is gene-driven (BRCA1 is
saturated with pathogenic submissions, a random intergenic gene is not), so a model can hit
high accuracy by memorising gene-level priors without ever looking at the variant. Every
split here keeps a gene wholly inside one fold.

Packing genes by size alone is not enough. Genes are internally correlated with label -- a
gene is often all-pathogenic or all-benign -- so a size-balanced split can still hand one
fold a 55% pathogenic rate and another 41%. The packer below tracks a quota per (fold, label)
and places each gene where it causes least overflow, which balances prevalence and size
together.
"""

from __future__ import annotations

import pandas as pd

Fractions = dict[str, float]


def gene_level_split(
    df: pd.DataFrame,
    fractions: Fractions,
    seed: int = 0,
    gene_col: str = "gene",
    label_col: str = "label",
    max_prevalence_spread: float = 0.05,
) -> pd.Series:
    """Assign each row a fold name; genes never straddle folds.

    Genes are visited largest-first (shuffled within size so ties are not alphabetical) and
    each is placed in the fold whose per-label quotas it overruns least. Returns a Series of
    fold names aligned to `df.index`.

    Raises if the resulting pathogenic rate varies by more than `max_prevalence_spread`
    across folds -- unbalanced folds make the arms incomparable, and it is better to fail
    here than to discover it in Phase 6.
    """
    if not fractions:
        raise ValueError("fractions must be non-empty")
    total_frac = sum(fractions.values())
    if abs(total_frac - 1.0) > 1e-6:
        raise ValueError(f"fractions must sum to 1.0, got {total_frac}")

    labels = sorted(df[label_col].unique()) if label_col in df.columns else ["_all"]
    work = df[[gene_col]].copy()
    work["_label"] = df[label_col] if label_col in df.columns else "_all"

    # counts[gene][label]
    counts = work.groupby([gene_col, "_label"]).size().unstack(fill_value=0)
    for lab in labels:
        if lab not in counts.columns:
            counts[lab] = 0
    counts = counts[labels]

    sizes = counts.sum(axis=1)
    order = (
        sizes.sample(frac=1.0, random_state=seed)
        .sort_values(ascending=False, kind="mergesort")  # stable: preserves shuffle in ties
        .index
    )

    totals = {lab: int(counts[lab].sum()) for lab in labels}
    quota = {f: {lab: fr * totals[lab] for lab in labels} for f, fr in fractions.items()}
    filled = {f: {lab: 0 for lab in labels} for f in fractions}
    assignment: dict[str, str] = {}

    for gene in order:
        gene_counts = {lab: int(counts.at[gene, lab]) for lab in labels}

        def cost(fold: str) -> tuple[float, float]:
            # primary: how far this gene pushes the fold past any of its per-label quotas
            overflow = sum(
                max(0.0, filled[fold][lab] + gene_counts[lab] - quota[fold][lab])
                for lab in labels
            )
            # tie-break: prefer the emptiest fold, so small genes top up the laggards
            deficit = sum(quota[fold][lab] - filled[fold][lab] for lab in labels)
            return (overflow, -deficit)

        fold = min(fractions, key=cost)
        assignment[gene] = fold
        for lab in labels:
            filled[fold][lab] += gene_counts[lab]

    folds = df[gene_col].map(assignment)

    if label_col in df.columns and "pathogenic" in labels:
        rates = df.assign(_fold=folds).groupby("_fold")[label_col].apply(
            lambda s: (s == "pathogenic").mean()
        )
        spread = float(rates.max() - rates.min())
        if spread > max_prevalence_spread:
            raise ValueError(
                "gene-level split produced unbalanced folds (pathogenic rate spread "
                f"{spread:.3f} > {max_prevalence_spread}): {rates.round(3).to_dict()}. "
                "Lower max_variants_per_gene, or raise max_prevalence_spread deliberately "
                "and record the choice in docs/eval_protocol.md."
            )

    return folds.rename("split")


def temporal_holdout(df: pd.DataFrame, cutoff: str, date_col: str = "last_evaluated") -> pd.Series:
    """Boolean mask of variants last evaluated after `cutoff`.

    Used for the memorization probe: variants classified after a generator's training cutoff
    cannot have been memorised from ClinVar itself. Rows with no date are False, not NaN --
    an unknown date is not evidence of recency.
    """
    dates = pd.to_datetime(df[date_col], errors="coerce")
    return (dates > pd.Timestamp(cutoff)).fillna(False).rename("is_holdout")
