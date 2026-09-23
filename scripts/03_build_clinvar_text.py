"""Phase 3: extract, sanitize and deduplicate ClinVar submitter comments for our variants.

    python scripts/03_build_clinvar_text.py

Writes data/processed/clinvar_comments.parquet (sanitized) and
results/sanitation_report.md. Compare the reported drop rate against the ~25% Li et al.
removed with a trained classifier -- a wildly different number means the rules are wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import clinvar_text, sanitize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submissions", default="data/raw/submission_summary.txt.gz")
    ap.add_argument("--variants", default="data/processed/variants.parquet")
    ap.add_argument("--out", default="data/processed/clinvar_comments.parquet")
    ap.add_argument("--report", default="results/sanitation_report.md")
    args = ap.parse_args()

    variants = pd.read_parquet(args.variants)
    ids = set(variants["variation_id"].astype(str))
    print(f"looking up submitter comments for {len(ids)} variants")

    df = clinvar_text.load_descriptions(Path(args.submissions), variation_ids=ids)
    print(f"found {len(df)} descriptions over {df['VariationID'].nunique()} variants")

    rep = sanitize.SanitationReport()
    df["sanitized"] = [sanitize.sanitize(t, rep) for t in df["Description"]]

    empty = int(df["sanitized"].str.strip().eq("").sum())
    df = df[df["sanitized"].str.strip().ne("")].reset_index(drop=True)

    # Dedupe within each variant: template reuse is per-lab, so cross-variant collapse would
    # wrongly merge distinct evidence.
    keep_idx: list[int] = []
    for _, grp in df.groupby("VariationID", sort=False):
        local = sanitize.dedupe(list(grp["sanitized"]), threshold=0.95)
        keep_idx.extend(grp.index[i] for i in local)
    before = len(df)
    df = df.loc[sorted(keep_idx)].reset_index(drop=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)

    per_variant = df.groupby("VariationID").size()
    covered = df["VariationID"].nunique()

    lines = [
        "# Sanitation report — ClinVar submitter comments",
        "",
        f"- variants in eval set: {len(ids)}",
        f"- variants with >=1 usable comment: {covered} ({covered / len(ids):.1%})",
        f"- descriptions found: {before + empty}",
        f"- emptied entirely by sanitation: {empty}",
        f"- near-duplicates collapsed (MinHash >=0.95): {before - len(df)}",
        f"- final comments retained: {len(df)}",
        "",
        "## Sentence-level sanitation",
        "",
        f"- sentences in: {rep.sentences_in}",
        f"- sentences kept: {rep.sentences_out}",
        f"- **drop rate: {rep.drop_rate:.1%}** (Li et al. 2026 removed ~25% with a trained "
        "classifier)",
        f"- dropped for stating a classification: {rep.dropped_label}",
        f"- dropped for verdict/conclusion framing: {rep.dropped_verdict}",
        f"- dropped for ACMG evidence codes: {rep.dropped_acmg}",
        "",
        "## Comments per variant (after sanitation and dedup)",
        "",
        f"- median {per_variant.median():.0f}, mean {per_variant.mean():.1f}, "
        f"max {per_variant.max()}" if len(per_variant) else "- none",
        "",
    ]
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-14:]))


if __name__ == "__main__":
    main()
