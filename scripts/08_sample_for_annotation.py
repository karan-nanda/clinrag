"""Phase 5: draw the human-validation sample and write annotator sheets.

    python scripts/08_sample_for_annotation.py --n 200 --annotators 2

Writes one CSV per annotator (identical rows, independently filled), the annotation guide,
and a key file mapping claim_id to the automatic label -- kept OUT of the annotator sheets
so nobody is anchored to the judge's answer.

Costs nothing: it only reads existing claims and verdicts.

Scoring the completed sheets:

    python scripts/08_sample_for_annotation.py --score \\
        --sheet-a annotation/annotator_a.csv --sheet-b annotation/annotator_b.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.metric import claims as claims_mod  # noqa: E402
from clinrag.metric import validation as val  # noqa: E402
from clinrag.metric import verify as verify_mod  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", default="results/judged/claims.jsonl")
    ap.add_argument("--verdicts", default="results/judged/verdicts.jsonl")
    ap.add_argument("--pools", default="data/processed/evidence_pools.jsonl")
    ap.add_argument("--out-dir", default="annotation")
    ap.add_argument("--n", type=int, default=200, help="protocol asks for 150-300")
    ap.add_argument("--annotators", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--score", action="store_true", help="score completed sheets instead")
    ap.add_argument("--sheet-a")
    ap.add_argument("--sheet-b")
    args = ap.parse_args()

    if args.score:
        return _score(args)

    if args.annotators < 2:
        raise SystemExit(
            "protocol §6 requires two independent annotators; agreement cannot be "
            "computed from one."
        )
    if not 150 <= args.n <= 300:
        print(f"WARNING: protocol §6 asks for 150-300 claims; you asked for {args.n}")

    cs = [claims_mod.Claim(**c) for c in load_jsonl(Path(args.claims))]
    vs = [verify_mod.Verdict(**v) for v in load_jsonl(Path(args.verdicts))]
    pools = {str(r["variation_id"]): r["delivered"] for r in load_jsonl(Path(args.pools))}
    print(f"{len(cs)} claims, {len(vs)} verdicts")

    sample = val.sample_for_annotation(cs, vs, n=args.n, seed=args.seed)
    if not sample:
        raise SystemExit("sampling produced nothing; check claims/verdicts line up by id")

    by_id = {v.claim_id: v for v in vs}
    dist: dict[str, int] = {}
    for c in sample:
        lab = by_id[c.claim_id].label
        dist[lab] = dist.get(lab, 0) + 1
    sf = sum(1 for c in sample if by_id[c.claim_id].same_family_as_generator)

    out = Path(args.out_dir)
    for i in range(args.annotators):
        name = chr(ord("a") + i)
        val.write_annotation_sheet(out / f"annotator_{name}.csv", sample, pools)

    # The key stays separate from the sheets on purpose.
    with (out / "_key.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("claim_id,auto_label,arm,scope,claim_type,same_family_as_generator\n")
        for c in sample:
            v = by_id[c.claim_id]
            fh.write(
                f"{c.claim_id},{v.label},{c.arm},{c.scope},{c.claim_type},"
                f"{v.same_family_as_generator}\n"
            )

    print(f"\nwrote {args.annotators} sheets x {len(sample)} claims -> {out}/")
    print(f"  automatic label distribution in sample: {dist}")
    print(f"  same-family-as-generator claims: {sf} ({sf / len(sample):.0%}) - over-sampled "
          "deliberately to measure judge self-preference")
    print(f"  key (auto labels) kept separate at {out}/_key.csv - do NOT give this to annotators")
    print("\nAnnotators: fill only the `label` column. Work independently; do not confer.")


def _score(args) -> None:
    if not (args.sheet_a and args.sheet_b):
        raise SystemExit("--score needs --sheet-a and --sheet-b")

    a = val.read_annotations(Path(args.sheet_a))
    b = val.read_annotations(Path(args.sheet_b))
    print(f"annotator A: {len(a)} labelled | annotator B: {len(b)} labelled")

    auto = {}
    key = Path(args.out_dir) / "_key.csv"
    if key.exists():
        import csv

        with key.open(encoding="utf-8") as fh:
            auto = {r["claim_id"]: r["auto_label"] for r in csv.DictReader(fh)}

    res = val.compare(a, b, auto or None)
    lines = [
        "# Metric validation",
        "",
        f"claims labelled by both annotators: {res['n_shared']}",
        "",
        "## Inter-annotator agreement",
        "",
        f"- {res['inter_annotator'].summary()}",
        "",
        "Low agreement here is a result about the task, not a bug to tune away: if humans",
        "cannot agree, the construct is underspecified and no judge can be validated",
        "against it.",
        "",
    ]
    if auto:
        lines += [
            "## Automatic metric vs humans",
            "",
            f"- vs annotator A: {res['auto_vs_a'].summary()}",
            f"- vs annotator B: {res['auto_vs_b'].summary()}",
        ]
        if "auto_vs_consensus" in res:
            lines += [
                f"- vs consensus ({res['n_consensus']} agreed claims): "
                f"{res['auto_vs_consensus'].summary()}",
                "",
                f"The {res['n_disagreed']} claims the annotators disagreed on are excluded",
                "from the consensus figure. Report all three: scoring only against consensus",
                "flatters the judge by removing every hard case.",
            ]
        lines += ["", "### Per-label, automatic vs annotator A", ""]
        for label, s in res["auto_vs_a"].per_label.items():
            lines.append(
                f"- `{label}`: support={s['support']} precision={s['precision']} "
                f"recall={s['recall']} f1={s['f1']}"
            )
        lines += [
            "",
            "A judge strong on `supported` and weak on `contradicted` is a judge that cannot",
            "find the failures this study is about.",
            "",
        ]

    path = Path("results/metric_validation.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
