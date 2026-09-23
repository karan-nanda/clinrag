"""Phase 4: generate explanations across the four arms.

Dry run, no API calls, no spend -- use this to verify prompts before paying for anything:

    python scripts/06_generate.py --split dev --backend echo --dry-run

Real run (costs money; batch is 50% cheaper and is the default path):

    python scripts/06_generate.py --split dev --backend claude --model claude-opus-5 --batch

Writes results/generations.jsonl and results/prompt_audit.md. The audit exists so the
structural-invariance claim in the protocol can be checked rather than asserted: it verifies
that the four arms differ only inside the EVIDENCE block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clinrag.generation import backends, distractors, prompts  # noqa: E402


def load_pools(path: Path) -> dict[str, list[dict]]:
    pools: dict[str, list[dict]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            pools[str(r["variation_id"])] = r["delivered"]
    return pools


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="data/processed/variants.parquet")
    ap.add_argument("--pools", default="data/processed/evidence_pools.jsonl")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--arms", default=",".join(prompts.ARMS))
    ap.add_argument("--backend", choices=("echo", "claude"), default="echo")
    ap.add_argument("--model", default=backends.OPUS)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--batch", action="store_true", help="submit via the Batch API (50%% cost)")
    ap.add_argument("--dry-run", action="store_true", help="build prompts only; never call out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="results/generations.jsonl")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in prompts.ARMS:
            raise SystemExit(f"unknown arm {a!r}; expected from {prompts.ARMS}")

    variants = pd.read_parquet(args.variants)
    if args.split != "all":
        variants = variants[variants["split"] == args.split]

    pools = load_pools(Path(args.pools))
    have = variants["variation_id"].astype(str).isin(pools)
    missing = int((~have).sum())
    variants = variants[have]
    if args.n:
        variants = variants.head(args.n)
    if variants.empty:
        raise SystemExit(
            f"no variants in split={args.split} have evidence pools. "
            "Run scripts/04_build_evidence_pools.py first."
        )
    print(f"{len(variants)} variants with pools" + (f" ({missing} without, skipped)" if missing else ""))

    records = variants.to_dict("records")
    assign = distractors.assign(records, seed=args.seed)
    print(f"distractor coverage: {distractors.coverage(assign)}")

    by_id = {str(v["variation_id"]): v for v in records}
    built = []
    skipped: dict[str, int] = {}
    noop: dict[str, int] = {}
    for v in records:
        vid = str(v["variation_id"])
        for arm in arms:
            passages = _passages_for(arm, vid, assign, pools)
            if passages is None:
                skipped[arm] = skipped.get(arm, 0) + 1
                continue

            meta: dict = {}
            if arm.startswith("distractor"):
                key = "same_gene" if arm == "distractor_same_gene" else "other_gene"
                donor_id = assign[vid][key]
                donor = by_id.get(donor_id, {})
                meta = distractors.describe_distractor(
                    pools.get(vid, []), passages, distractors.donor_keys(donor)
                )
                meta["donor_id"] = donor_id
                # A distractor identical to the grounded evidence is not a distractor. Keeping
                # it would drag the arm's effect toward zero for a purely mechanical reason.
                if meta["identical_to_grounded"]:
                    noop[arm] = noop.get(arm, 0) + 1
                    continue

            built.append(prompts.build(v, arm, passages, meta=meta))

    if skipped:
        print(f"skipped for want of a distractor: {skipped}")
    if noop:
        print(f"dropped - distractor evidence identical to grounded: {noop}")
    print(f"{len(built)} prompts built across {len(arms)} arms")
    _distractor_report(built)

    _audit(built, Path("results/prompt_audit.md"), arms)

    if args.dry_run:
        print("\ndry run - no API calls made, nothing spent.")
        _preview(built)
        return

    gen = backends.EchoGenerator() if args.backend == "echo" else backends.ClaudeGenerator(
        model=args.model, effort=args.effort
    )
    results = _run_batch(gen, built) if args.batch else _run_serial(gen, built)

    backends.write_jsonl(results, args.out)
    ok = sum(1 for r in results if not r.error)
    print(f"\nwrote {len(results)} generations ({ok} ok, {len(results) - ok} errored) -> {args.out}")


def _passages_for(arm, vid, assign, pools) -> list[dict] | None:
    if arm == "ungrounded":
        return []
    if arm == "grounded":
        return pools.get(vid)
    key = "same_gene" if arm == "distractor_same_gene" else "other_gene"
    donor = assign.get(vid, {}).get(key)
    return pools.get(donor) if donor else None


def _run_serial(gen, built) -> list:
    out = []
    for i, p in enumerate(built, 1):
        g = gen.generate(p.system, p.user)
        g.variation_id, g.arm, g.meta = p.variation_id, p.arm, p.meta
        out.append(g)
        if i % 25 == 0 or i == len(built):
            print(f"  [{i}/{len(built)}]")
    return out


def _run_batch(gen, built) -> list:
    items = [
        (backends.make_custom_id(p.variation_id, p.arm, gen.name), p.system, p.user)
        for p in built
    ]
    batch_id = gen.submit_batch(items)
    print(f"submitted batch {batch_id} ({len(items)} requests); polling...")
    gen.wait(batch_id)
    results = gen.collect(batch_id)
    # Batch results come back unordered; re-attach prompt metadata by (variant, arm).
    meta = {(p.variation_id, p.arm): p.meta for p in built}
    for r in results:
        r.meta = meta.get((r.variation_id, r.arm), {})
    return results


def _distractor_report(built) -> None:
    """How many distractor prompts can actually test conflation.

    The model can only notice that evidence describes the wrong variant if the evidence
    names a variant. Where it names none, generic gene-level text is not detectably wrong
    for the target, so pooling those with the rest understates the arm.
    """
    for arm in ("distractor_same_gene", "distractor_other_gene"):
        rows = [p.meta for p in built if p.arm == arm]
        if not rows:
            continue
        n = len(rows)
        named = sum(1 for m in rows if m.get("names_donor"))
        any_var = sum(1 for m in rows if m.get("names_a_variant"))
        print(
            f"  {arm}: n={n}  evidence names donor variant={named} ({named / n:.0%})  "
            f"names some variant={any_var} ({any_var / n:.0%})"
        )


def _audit(built, path: Path, arms: list[str]) -> None:
    """Verify the arms differ only inside the EVIDENCE block."""
    by_variant: dict[str, dict[str, str]] = {}
    for p in built:
        by_variant.setdefault(p.variation_id, {})[p.arm] = p.user

    mismatches = []
    for vid, per_arm in by_variant.items():
        shells = {arm: _shell(text) for arm, text in per_arm.items()}
        if len(set(shells.values())) > 1:
            mismatches.append(vid)

    complete = sum(1 for d in by_variant.values() if len(d) == len(arms))
    lines = [
        "# Prompt audit",
        "",
        f"variants: {len(by_variant)}  arms: {arms}",
        f"variants with all {len(arms)} arms built: {complete}",
        "",
        "## Structural invariance",
        "",
        "The protocol requires that arms differ ONLY inside the EVIDENCE block. This strips",
        "each prompt's evidence section and compares what remains.",
        "",
        f"- prompts whose non-evidence text differs across arms: **{len(mismatches)}**",
    ]
    lines.append(
        "- PASS: structure is identical across arms." if not mismatches
        else f"- **FAIL** on: {mismatches[:10]}"
    )
    lines += ["", "## System prompt", "", "```", built[0].system if built else "", "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(("prompt audit: PASS" if not mismatches else f"prompt audit: FAIL ({len(mismatches)})"))


def _shell(user_text: str) -> str:
    """Prompt with the evidence contents removed, for structural comparison."""
    head, _, rest = user_text.partition("\nEVIDENCE\n")
    _, _, tail = rest.partition("\nTASK\n")
    return head + "\n<EVIDENCE>\n" + tail


def _preview(built) -> None:
    seen = set()
    for p in built:
        if p.arm in seen:
            continue
        seen.add(p.arm)
        print(f"\n{'=' * 70}\nARM: {p.arm}\n{'=' * 70}")
        print(p.user[:900])
        if len(p.user) > 900:
            print(f"... [{len(p.user)} chars total]")


if __name__ == "__main__":
    main()
