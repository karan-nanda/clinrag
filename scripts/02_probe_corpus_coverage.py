"""Phase 3 probe: how much variant-level evidence actually exists per variant?

This is the go/no-go measurement for the grounded arm. If the median variant has zero
variant-level passages, then "grounded" mostly means "handed gene-level evidence", the
distractor_same_gene arm stops being a contrast, and the headline comparison is not the one
we think we are running.

Run it before committing to generation cost:

    python scripts/02_probe_corpus_coverage.py --n 25 --pages 3

Writes results/corpus_coverage.csv and prints the distribution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import pubtator as pt  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="data/processed/variants.parquet")
    ap.add_argument("--split", default="dev", help="probe on dev; keep test unseen")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--cache", default="data/interim/pubtator")
    ap.add_argument("--out", default="results/corpus_coverage.csv")
    args = ap.parse_args()

    df = pd.read_parquet(args.variants)
    df = df[df["split"] == args.split]
    sample = df.sample(n=min(args.n, len(df)), random_state=0)

    cache = Path(args.cache)
    rows = []
    for i, (_, v) in enumerate(sample.iterrows(), 1):
        rsid = v["rsid"] if pd.notna(v["rsid"]) else None
        try:
            raw = pt.corpus_for_variant(v["gene"], rsid, cache_dir=cache, max_pages=args.pages)
        except Exception as exc:  # network flakiness should not lose the whole run
            print(f"  [{i}/{len(sample)}] {v['gene']} {rsid} FAILED: {exc}")
            rows.append({"gene": v["gene"], "rsid": rsid, "label": v["label"], "error": str(exc)})
            continue

        pool = pt.prepare_pool(raw, v["gene"], rsid)
        wide = pt.prepare_pool(
            raw, v["gene"], rsid, sections=pt.ABSTRACT_SECTIONS | pt.BODY_SECTIONS
        )
        s_abs = pt.pool_stats(pool, v["gene"], rsid)
        s_wide = pt.pool_stats(wide, v["gene"], rsid)
        rows.append(
            {
                "gene": v["gene"],
                "rsid": rsid,
                "label": v["label"],
                "raw_passages": len(raw),
                "abs_pool": s_abs["n"],
                "abs_variant_level": s_abs["variant_level"],
                "abs_gene_level": s_abs["gene_level"],
                "wide_pool": s_wide["n"],
                "wide_variant_level": s_wide["variant_level"],
                "pmids": s_abs["pmids"],
            }
        )
        print(
            f"  [{i}/{len(sample)}] {v['gene']:<10} {str(rsid):<14} "
            f"abstracts={s_abs['n']:<4} variant-level={s_abs['variant_level']:<3} "
            f"(+body {s_wide['variant_level']})"
        )

    out = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    _report(out)


def _report(out: pd.DataFrame) -> None:
    ok = out[out.get("abs_pool").notna()] if "abs_pool" in out else out
    if ok.empty:
        print("\nno successful probes")
        return

    print("\n" + "=" * 62)
    print(f"variants probed: {len(ok)}   (errors: {len(out) - len(ok)})")
    for col, name in [
        ("abs_variant_level", "variant-level passages (abstracts only)"),
        ("wide_variant_level", "variant-level passages (+ body text)"),
        ("abs_pool", "total pool size (abstracts only)"),
    ]:
        s = ok[col]
        print(
            f"\n{name}:\n"
            f"  zero: {int((s == 0).sum())}/{len(s)}   median: {s.median():.0f}   "
            f"mean: {s.mean():.1f}   max: {int(s.max())}"
        )

    zero = (ok["abs_variant_level"] == 0).mean()
    print("\n" + "-" * 62)
    if zero > 0.5:
        print(
            f"WARNING: {zero:.0%} of variants have NO variant-level evidence in abstracts.\n"
            "The grounded arm would mostly be receiving gene-level evidence. Options:\n"
            "  (a) include body paragraphs in the pool (changes what 'the corpus' means),\n"
            "  (b) add LitVar2 / ClinVar submitter comments as sources,\n"
            "  (c) restrict the eval set to variants with >=1 variant-level passage and\n"
            "      report that selection explicitly as a limitation."
        )
    else:
        print(f"{zero:.0%} of variants have no variant-level evidence in abstracts.")
    print("=" * 62)


if __name__ == "__main__":
    main()
