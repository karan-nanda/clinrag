"""Phase 3: assemble the per-variant evidence pool and the evidence actually delivered.

Combines both sources, deduplicates, ranks with BM25, and applies the evidence budget:

    python scripts/04_build_evidence_pools.py --split dev --k 10 --max-chars 6000

Writes data/processed/evidence_pools.jsonl (one JSON object per variant) and
results/evidence_budget.md. The budget report is the point -- it checks that pathogenic and
benign variants receive comparable amounts of evidence, closing the length cue described in
docs/eval_protocol.md §10.

PubTator3 is rate limited to ~3 req/s, so a full 1,000-variant build takes roughly an hour.
Fetches are cached under data/interim/pubtator, so re-runs are fast.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import pubtator as pt  # noqa: E402
from clinrag.retrieval import sanitize  # noqa: E402
from clinrag.retrieval.index import (  # noqa: E402
    BM25Retriever,
    Evidence,
    apply_budget,
    budget_report,
    variant_query,
)


def build_pool(v: pd.Series, comments: pd.DataFrame, cache: Path, pages: int) -> list[Evidence]:
    """Literature passages + sanitized submitter comments for one variant, deduplicated."""
    rsid = v["rsid"] if pd.notna(v["rsid"]) else None
    pool: list[Evidence] = []

    try:
        raw = pt.corpus_for_variant(v["gene"], rsid, cache_dir=cache, max_pages=pages)
        for p in pt.prepare_pool(raw, v["gene"], rsid):
            pool.append(
                Evidence(
                    doc_id=p.passage_id,
                    source="pubtator",
                    text=p.text,
                    level=p.evidence_level(v["gene"], rsid),
                    year=p.year,
                    meta={"pmid": p.pmid, "section": p.section, "journal": p.journal},
                )
            )
    except Exception as exc:
        print(f"    pubtator failed for {v['gene']} {rsid}: {exc}")

    mine = comments[comments["VariationID"] == str(v["variation_id"])]
    for _, c in mine.iterrows():
        pool.append(
            Evidence(
                doc_id=str(c["SCV"]),
                source="clinvar_comment",
                text=c["sanitized"],
                level="variant",  # submitted against this exact variant
                year=(c["DateLastEvaluated"].year if pd.notna(c["DateLastEvaluated"]) else None),
                meta={"submitter": c["Submitter"], "review_status": c["ReviewStatus"]},
            )
        )

    keep = sanitize.dedupe([e.text for e in pool], threshold=0.95)
    return [pool[i] for i in keep]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="data/processed/variants.parquet")
    ap.add_argument("--comments", default="data/processed/clinvar_comments.parquet")
    ap.add_argument("--split", default="dev", help="'all' for every split")
    ap.add_argument("--n", type=int, default=0, help="cap variants processed (0 = no cap)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--variant-slots", type=int, default=1,
                    help="reserved AND capped variant-level passages per prompt")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--cache", default="data/interim/pubtator")
    ap.add_argument("--out", default="data/processed/evidence_pools.jsonl")
    ap.add_argument("--report", default="results/evidence_budget.md")
    args = ap.parse_args()

    variants = pd.read_parquet(args.variants)
    if args.split != "all":
        variants = variants[variants["split"] == args.split]
    if args.n:
        variants = variants.head(args.n)

    comments = pd.read_parquet(args.comments)
    comments["VariationID"] = comments["VariationID"].astype(str)

    cache = Path(args.cache)
    delivered: dict[str, list[Evidence]] = {}
    labels: dict[str, str] = {}
    rows = []

    for i, (_, v) in enumerate(variants.iterrows(), 1):
        vid = str(v["variation_id"])
        pool = build_pool(v, comments, cache, args.pages)

        query = variant_query(
            v["gene"],
            rsid=v["rsid"] if pd.notna(v["rsid"]) else None,
            phenotypes=v["phenotypes"] if pd.notna(v.get("phenotypes")) else None,
        )
        # Rank the WHOLE pool, not a top-n slice. Variant-level comments routinely score
        # below 30 gene-level abstracts -- a comment says "this variant", while a gene
        # abstract repeats the gene symbol -- so truncating before `apply_budget` starved
        # the reserved variant-level slot on 4 of 30 dev variants, all benign.
        ranked = BM25Retriever(pool).search(query, k=len(pool))
        chosen = apply_budget(
            ranked, k=args.k, max_chars=args.max_chars,
            variant_level_slots=args.variant_slots,
        )

        delivered[vid] = chosen
        labels[vid] = v["label"]
        rows.append(
            {
                "variation_id": vid,
                "gene": v["gene"],
                "rsid": v["rsid"] if pd.notna(v["rsid"]) else None,
                "label": v["label"],
                "split": v["split"],
                "pool_size": len(pool),
                "delivered": [e.as_dict() for e in chosen],
            }
        )
        if i % 10 == 0 or i == len(variants):
            print(f"  [{i}/{len(variants)}] pools built")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    _report(rows, delivered, labels, args)


def _report(rows, delivered, labels, args) -> None:
    df = pd.DataFrame([{k: r[k] for k in ("label", "pool_size", "delivered")} for r in rows])
    df["n_delivered"] = df["delivered"].apply(len)
    df["chars"] = df["delivered"].apply(lambda ds: sum(len(d["text"]) for d in ds))
    df["n_variant_level"] = df["delivered"].apply(
        lambda ds: sum(1 for d in ds if d["level"] == "variant")
    )
    df["n_comment"] = df["delivered"].apply(
        lambda ds: sum(1 for d in ds if d["source"] == "clinvar_comment")
    )

    bud = budget_report(delivered, labels)
    ratio = bud.get("char_ratio", float("nan"))
    by_label = df.groupby("label")[
        ["pool_size", "n_delivered", "chars", "n_variant_level", "n_comment"]
    ].mean().round(2)

    lines = [
        "# Evidence budget report",
        "",
        f"split={args.split}  k={args.k}  max_chars={args.max_chars}  "
        f"variant_slots={args.variant_slots}  variants={len(df)}",
        "",
        "## Delivered evidence by label (means)",
        "",
        "```",
        by_label.to_string(),
        "```",
        "",
        f"**Character ratio between classes: {ratio}**",
        "",
    ]
    if isinstance(ratio, float) and ratio == ratio:
        if ratio <= 1.15:
            lines.append(
                "Within 15% -- the volume cue described in eval_protocol.md §10 is closed at "
                "this budget."
            )
        else:
            lines.append(
                f"**WARNING: {ratio}x difference remains.** The length cue survives the "
                "budget: one class is still receiving materially more text. Lower "
                "`--max-chars` or `--k` until the ratio approaches 1.0, and re-check before "
                "any generation run."
            )
    lines += [
        "",
        "## Source mix",
        "",
        f"- mean delivered passages that are variant-level: "
        f"{df['n_variant_level'].mean():.2f} of {df['n_delivered'].mean():.2f}",
        f"- mean delivered passages from ClinVar comments: {df['n_comment'].mean():.2f}",
        "",
        "A pool that is overwhelmingly gene-level weakens the `distractor_same_gene` contrast;",
        "track this number per arm in Phase 4.",
        "",
    ]
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
