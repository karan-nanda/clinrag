"""Phase 3: evaluate retrieval on its own against the expert-citation gold set.

    python scripts/05_eval_retrieval.py --split dev --pages 3

Reports two separate numbers, because they have different fixes:

* `pool_recall` -- is the expert-cited paper in the candidate pool at all? A miss here is a
  CORPUS problem: widen the search, add LitVar2, raise --pages.
* `recall@k`    -- given it is reachable, does ranking surface it? A miss here is a RANKING
  problem: the dense/hybrid retriever's job.

Without this split, a corpus failure is indistinguishable from a retriever failure, and a
weak `grounded` arm in Phase 6 cannot be diagnosed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.retrieval import goldset, pubtator as pt  # noqa: E402
from clinrag.retrieval.index import BM25Retriever, Evidence, apply_budget, variant_query  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="data/processed/variants.parquet")
    ap.add_argument("--comments", default="data/processed/clinvar_comments.parquet")
    ap.add_argument("--split", default="dev", help="'all' for every split")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--cached-only", action="store_true",
                    help="skip variants with no cached PubTator fetch (no network)")
    ap.add_argument("--cache", default="data/interim/pubtator")
    ap.add_argument("--report", default="results/retrieval_eval.md")
    args = ap.parse_args()

    variants = pd.read_parquet(args.variants)
    if args.split != "all":
        variants = variants[variants["split"] == args.split]

    comments = pd.read_parquet(args.comments)
    comments["VariationID"] = comments["VariationID"].astype(str)
    gold = goldset.build(comments)          # from RAW Description; see goldset docstring
    print(f"gold set: {len(gold)} variants with >=1 expert-cited PMID")

    variants["vid"] = variants["variation_id"].astype(str)
    covered = variants[variants["vid"].isin(gold)]
    if args.n:
        covered = covered.head(args.n)
    print(f"evaluating {len(covered)} variants in split={args.split}")

    cache = Path(args.cache)
    triples = []
    skipped = 0

    for i, (_, v) in enumerate(covered.iterrows(), 1):
        rsid = v["rsid"] if pd.notna(v["rsid"]) else None
        key = f"{v['gene']}__{rsid or 'nors'}".replace("/", "_")
        if args.cached_only and not (cache / f"{key}.json").exists():
            skipped += 1
            continue
        try:
            raw = pt.corpus_for_variant(v["gene"], rsid, cache_dir=cache, max_pages=args.pages)
        except Exception as exc:
            print(f"  {v['gene']} {rsid} fetch failed: {exc}")
            continue

        passages = pt.prepare_pool(raw, v["gene"], rsid)
        pool = [
            Evidence(
                doc_id=p.passage_id, source="pubtator", text=p.text,
                level=p.evidence_level(v["gene"], rsid), year=p.year, meta={"pmid": p.pmid},
            )
            for p in passages
        ]
        pool_pmids = {p.pmid for p in passages}

        query = variant_query(v["gene"], rsid=rsid)
        ranked = BM25Retriever(pool).search(query, k=len(pool))
        delivered = apply_budget(ranked, k=args.k, max_chars=10**9, variant_level_slots=0)
        ranked_pmids = [{e.meta.get("pmid")} for e in delivered]

        triples.append((gold[v["vid"]], pool_pmids, ranked_pmids))
        if i % 20 == 0:
            print(f"  [{i}/{len(covered)}]")

    sc = goldset.score(triples, ks=(1, 3, 5, args.k))
    _report(sc, args, len(covered), skipped)


def _report(sc, args, n_covered: int, skipped: int) -> None:
    ks = sorted((sc.recall_at_k or {}).items())
    lines = [
        "# Retrieval evaluation — expert-citation gold set",
        "",
        f"split={args.split}  k={args.k}  pages={args.pages}  "
        f"evaluated={sc.variants}/{n_covered}" + (f"  (skipped {skipped} uncached)" if skipped else ""),
        "",
        "Gold labels are PMIDs cited by ClinVar submitters for that exact variant — expert",
        "relevance judgments, independent of our retriever. Extracted from raw descriptions;",
        "they are never shown to a generator.",
        "",
        "## Stage 1 — is the cited paper in the candidate pool at all? (corpus)",
        "",
        f"- **pool_recall = {sc.pool_recall:.3f}**  "
        f"({sc.reachable_pmids}/{sc.gold_pmids} cited PMIDs reachable)",
        "",
        "## Stage 2 — given it is reachable, does ranking surface it? (retriever)",
        "",
    ]
    lines += [f"- recall@{k} = {v:.3f}" for k, v in ks]
    lines += [
        "",
        "Stage 2 is scored only over reachable papers, so a corpus miss is not charged to the",
        "ranker twice.",
        "",
        "## Reading this",
        "",
    ]
    if sc.pool_recall < 0.5:
        lines.append(
            f"**Corpus-bound.** {1 - sc.pool_recall:.0%} of expert-cited papers are not in the "
            "candidate pool, so no retriever can reach them. A better ranker will not fix "
            "this — raise `--pages`, add LitVar2, or accept it and state the ceiling."
        )
    else:
        lines.append(f"Pool reaches {sc.pool_recall:.0%} of expert-cited papers.")
    top = ks[-1][1] if ks else float("nan")
    if top == top and top < 0.7:
        lines.append(
            f"\n**Ranking-bound.** Only {top:.0%} of reachable papers reach the top-{args.k}. "
            "This is the dense/hybrid retriever's job."
        )
    lines.append("")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
