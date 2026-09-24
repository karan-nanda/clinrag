"""Phase 5: decompose generated explanations into claims, verify them, and report rates.

Free end-to-end run (rule-based decomposer, lexical verifier, no API calls):

    python scripts/07_score_faithfulness.py

Real run:

    python scripts/07_score_faithfulness.py --decomposer llm --verifier llm

Writes results/claims.jsonl, results/verdicts.jsonl and results/faithfulness.md.

Read the report with the protocol in hand. `unsupported` is not `false`: the corpus is
demonstrably thin (protocol §12), so a high unsupported rate is partly a statement about the
evidence, not only about the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.metric import aggregate, claims as claims_mod, verify as verify_mod  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generations", default="results/generations.jsonl")
    ap.add_argument("--pools", default="data/processed/evidence_pools.jsonl")
    ap.add_argument("--decomposer", choices=("sentence", "llm"), default="sentence")
    ap.add_argument("--verifier", choices=("lexical", "llm"), default="lexical")
    ap.add_argument("--model", default="claude-opus-5", help="model for llm decomposer/judge")
    ap.add_argument("--n-boot", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    gens = load_jsonl(Path(args.generations))
    gens = [g for g in gens if g.get("text") and not g.get("error")]
    if not gens:
        raise SystemExit(f"no usable generations in {args.generations}")

    pools = {
        str(r["variation_id"]): r["delivered"] for r in load_jsonl(Path(args.pools))
    }
    print(f"{len(gens)} generations, {len(pools)} evidence pools")

    decomposer = (
        claims_mod.SentenceDecomposer() if args.decomposer == "sentence"
        else claims_mod.LLMDecomposer(model=args.model)
    )
    verifier = (
        verify_mod.LexicalVerifier() if args.verifier == "lexical"
        else verify_mod.LLMJudge(model=args.model)
    )
    print(f"decomposer={decomposer.name}  verifier={verifier.name}")
    if args.verifier == "llm":
        print("  NOTE: judge and generator may share a model family; see protocol §14")

    all_claims, all_verdicts = [], []
    strata: dict[tuple[str, str], bool] = {}

    for i, g in enumerate(gens, 1):
        vid, arm = str(g["variation_id"]), g["arm"]
        cs = decomposer.decompose(g["text"], vid, arm, g.get("model", "?"))

        # The ungrounded arm has no evidence block, so every claim there is verified against
        # the SAME pool the grounded arm saw. Otherwise "unsupported" would be guaranteed by
        # construction and the comparison would be circular.
        pool = pools.get(vid, [])

        for c in cs:
            all_verdicts.append(verifier.verify(c, pool, g.get("model", "")))
        all_claims.extend(cs)

        meta = g.get("meta") or {}
        if "names_donor" in meta:
            strata[(vid, arm)] = bool(meta["names_donor"])
        if i % 100 == 0 or i == len(gens):
            print(f"  [{i}/{len(gens)}] {len(all_claims)} claims")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "claims.jsonl", [c.as_dict() for c in all_claims])
    _write(out / "verdicts.jsonl", [v.as_dict() for v in all_verdicts])

    rates = aggregate.explanation_rates(all_claims, all_verdicts)
    _report(out / "faithfulness.md", rates, all_claims, all_verdicts, strata, args,
            decomposer.name, verifier.name)


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _report(path, rates, all_claims, all_verdicts, strata, args, dec_name, ver_name) -> None:
    summary = aggregate.arm_summary(rates)
    arms = list(summary)

    lines = [
        "# Faithfulness report",
        "",
        f"decomposer=`{dec_name}`  verifier=`{ver_name}`  "
        f"claims={len(all_claims)}  explanations={len(rates)}",
        "",
        "> `unsupported` means the evidence pool is silent on the claim. It is **not** a",
        "> synonym for false. The corpus is thin by measurement (protocol §12), so this rate",
        "> describes the evidence as much as the model.",
        "",
        "## Rates by arm",
        "",
        "| arm | expl | claims | mean claims | supported | unsupported | contradicted | conflation |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm in arms:
        s = summary[arm]
        lines.append(
            f"| `{arm}` | {s['n_explanations']} | {s['n_claims']} | {s['mean_claims']} | "
            f"{s['supported']:.3f} | {s['unsupported']:.3f} | {s['contradicted']:.3f} | "
            f"{s['conflation']:.3f} |"
        )

    # A verifier that assigns every claim the same label has measured nothing. Reporting
    # arm differences from it would be reporting noise around a constant.
    labels_seen = {v.label for v in all_verdicts}
    if len(labels_seen) == 1:
        only = next(iter(labels_seen))
        lines += [
            "",
            f"> **DEGENERATE: every claim was labelled `{only}`.** This verifier has not "
            "measured anything, and the arm comparisons below are differences between "
            "constants. On the pilot, no claim reached the lexical baseline's 0.50 overlap "
            "threshold (max observed 0.471) because models paraphrase rather than copy. "
            "Do not report these numbers. Use `--verifier llm`, or calibrate the threshold "
            "against human labels first -- never by eye.",
            "",
        ]

    lines += ["", "## Paired comparisons vs `ungrounded`", "",
              "Bootstrap resampled over **variants**, not claims, and paired across arms.", ""]
    if "ungrounded" in arms:
        for arm in arms:
            if arm == "ungrounded":
                continue
            for metric in ("unsupported", "contradicted", "conflation"):
                try:
                    res = aggregate.bootstrap_diff(
                        rates, arm, "ungrounded", metric=metric,
                        n_boot=args.n_boot, seed=args.seed,
                    )
                    lines.append(f"- {res.summary()}")
                except ValueError as exc:
                    lines.append(f"- {arm}/{metric}: skipped ({exc})")
    else:
        lines.append("- no `ungrounded` arm present; nothing to compare against")

    if strata:
        lines += ["", "## Distractor arms stratified by `names_donor`", "",
                  "Where the distractor evidence names no variant, no conflation signal can",
                  "exist; pooling would dilute the effect (protocol §13).", ""]
        for arm in arms:
            if not arm.startswith("distractor"):
                continue
            for metric in ("unsupported", "conflation"):
                got = aggregate.stratified_summary(rates, strata, arm, metric)
                lines.append(f"- `{arm}` {metric}: " + ", ".join(
                    f"{k}={v['mean']} (n={v['n']})" for k, v in got.items() if v["n"]
                ))

    types: dict[str, int] = {}
    for c in all_claims:
        types[c.claim_type] = types.get(c.claim_type, 0) + 1
    scopes: dict[str, int] = {}
    for c in all_claims:
        scopes[c.scope] = scopes.get(c.scope, 0) + 1

    lines += [
        "", "## Claim composition", "",
        "By type: " + ", ".join(f"{k}={v}" for k, v in sorted(types.items(), key=lambda t: -t[1])),
        "", "By scope: " + ", ".join(f"{k}={v}" for k, v in sorted(scopes.items(), key=lambda t: -t[1])),
        "",
    ]

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
