"""Phase 1 driver: ClinVar dump -> labelled, gene-split variant set.

    python scripts/01_build_variant_set.py --config configs/data.yaml

Writes to data/processed/:
    variants.parquet        main set, balanced P/B, with a `split` column
    variants_vus.parquet    VUS pool for the secondary analysis (not split)
    variant_set_card.md     provenance + counts, so the paper can cite exact numbers
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.data import clinvar, splits  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/data.yaml")
    ap.add_argument("--skip-download", action="store_true", help="use the local dump as-is")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    raw = Path(cfg["clinvar"]["raw_path"])

    if not args.skip_download:
        clinvar.download(cfg["clinvar"]["url"], raw)
    if not raw.exists():
        raise SystemExit(f"{raw} not found; drop the ClinVar dump there or omit --skip-download")

    f = cfg["filters"]
    spec = clinvar.FilterSpec(
        min_review_stars=f["min_review_stars"],
        variant_types=tuple(t.lower() for t in f["variant_types"]),
        include_vus=f["include_vus"],
        drop_somatic=f["drop_somatic"],
        assembly=cfg["clinvar"]["assembly"],
    )

    df = clinvar.load_and_filter(raw, spec)
    vus = df[df["label"] == "vus"].reset_index(drop=True)
    labelled = df[df["label"].isin(("pathogenic", "benign"))].reset_index(drop=True)

    # Evidence availability is confounded with the label: on a first build, 95%+ of
    # pathogenic variants had a usable submitter comment against ~20% of benign ones. Left
    # alone, the mere PRESENCE of evidence predicts the label, which sanitizing sentence text
    # cannot fix. Restricting the eval set to variants that have evidence on both sides
    # removes the confound, at the cost of selecting better-studied variants -- a selection
    # bias that must be stated in the limitations.
    if f.get("require_submitter_comment"):
        labelled = _require_evidence(labelled, cfg)

    s = cfg["sampling"]
    if s.get("pair_by_gene"):
        # Same-gene same-label pairs, so every variant has a distractor_same_gene partner.
        main_set = clinvar.balance_paired(labelled, target_n=s["target_n"], seed=s["seed"])
    else:
        main_set = clinvar.balance(
            labelled,
            target_n=s["target_n"],
            max_per_gene=s["max_variants_per_gene"],
            seed=s["seed"],
        )

    sp = cfg["split"]
    if sp["by"] != "gene":
        raise SystemExit("split.by must be 'gene' - see docs/PLAN.md Phase 1")
    main_set["split"] = splits.gene_level_split(
        main_set, fractions=sp["fractions"], seed=s["seed"]
    )
    if sp.get("temporal_holdout_after"):
        main_set["is_holdout"] = splits.temporal_holdout(
            main_set, sp["temporal_holdout_after"]
        )

    out = Path(cfg["paths"]["processed"])
    out.mkdir(parents=True, exist_ok=True)
    main_set.to_parquet(out / "variants.parquet", index=False)
    vus.to_parquet(out / "variants_vus.parquet", index=False)
    (out / "variant_set_card.md").write_text(_card(cfg, df, labelled, main_set, vus))

    print(f"\nwrote {len(main_set)} variants across {main_set['gene'].nunique()} genes -> {out}")
    print(main_set.groupby(["split", "label"]).size().unstack(fill_value=0))


def _require_evidence(labelled: pd.DataFrame, cfg) -> pd.DataFrame:
    """Keep only variants with at least one submitter comment that survives sanitation."""
    from clinrag.retrieval import clinvar_text, sanitize

    sub = Path(cfg["clinvar"].get("submissions_path", "data/raw/submission_summary.txt.gz"))
    if not sub.exists():
        raise SystemExit(
            f"{sub} not found. require_submitter_comment needs the ClinVar submission dump; "
            "fetch it or set filters.require_submitter_comment: false"
        )

    ids = set(labelled["variation_id"].astype(str))
    desc = clinvar_text.load_descriptions(sub, variation_ids=ids)
    desc["sanitized"] = [sanitize.sanitize(t) for t in desc["Description"]]
    with_evidence = set(desc.loc[desc["sanitized"].str.strip().ne(""), "VariationID"])

    before = labelled.groupby("label").size().to_dict()
    out = labelled[labelled["variation_id"].astype(str).isin(with_evidence)].reset_index(drop=True)
    after = out.groupby("label").size().to_dict()
    print(f"  evidence filter: {before} -> {after}")
    return out


def _card(cfg, df: pd.DataFrame, labelled: pd.DataFrame, main: pd.DataFrame, vus: pd.DataFrame) -> str:
    by_split = main.groupby(["split", "label"]).size().unstack(fill_value=0)
    lines = [
        "# Variant set card",
        "",
        f"Built {date.today().isoformat()} from `{cfg['clinvar']['url']}`",
        f"({cfg['clinvar']['assembly']}).",
        "",
        "## Filters",
        f"- review status >= {cfg['filters']['min_review_stars']} stars",
        f"- types: {cfg['filters']['variant_types']}",
        f"- somatic dropped: {cfg['filters']['drop_somatic']}",
        f"- max {cfg['sampling']['max_variants_per_gene']} variants per gene per class",
        f"- seed {cfg['sampling']['seed']}",
        "",
        "## Counts",
        f"- passed filters: {len(df)} ({len(labelled)} P/B, {len(vus)} VUS)",
        f"- main set: {len(main)} over {main['gene'].nunique()} genes",
        f"- pathogenic {int((main['label'] == 'pathogenic').sum())} / "
        f"benign {int((main['label'] == 'benign').sum())}",
        "",
        "## Split (gene-level; no gene appears in two folds)",
        "",
        "```",
        by_split.to_string(),
        "```",
        "",
        "VUS are excluded from the main experiment (no ground truth) and kept in",
        "`variants_vus.parquet` for the secondary analysis.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
