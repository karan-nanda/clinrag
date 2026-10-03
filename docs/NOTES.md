# Working notes — pick up here

Last worked: **2026-10-02**. Phases 1, 3, 4 and 5 are built and have been exercised end to
end against real models. 115 tests pass. Branch `phase5-faithfulness`, pushed, in sync.

Read `docs/eval_protocol.md` for the binding decisions (§10–§14 hold the measurements that
forced them), `docs/pilot_results.md` for the first real numbers and their caveats, and
`docs/related_work.md` §1 for why the sanitizer exists.

---

## In plain English — where this is

**What is running.** A job is out asking the AI to write 590 variant explanations: 150
variants, four versions each (with evidence, without evidence, and two kinds of deliberately
wrong evidence). About $11. Results come back within a day. The earlier run covered only 10
variants — enough to prove the machinery worked, far too few to trust any number from. 150 is
enough to start believing the results.

**Why only half the job was paid for.** Writing the explanations is the cheap part (~$11).
Grading them — checking sentence by sentence whether the evidence actually backs each claim —
is the expensive part (~$50). The grader is itself an AI, and nobody has checked yet whether
it is any good. The explanations are useful whatever happens: if the grader turns out to be
unreliable, there are still 590 good explanations that can be graded another way. Fifty
dollars of grading by a bad grader is fifty dollars wasted. So: cheap half now, expensive
half once the grader is known to work.

**What is actually holding everything up, and it is not the computer.** Two people each need
to read 50 statements and say, for each one, whether the evidence supports it, says nothing
about it, or contradicts it. Roughly 90 minutes each.

That is the whole blocker. Until two humans do it, there is no way to know whether the AI
grader agrees with human judgement — and if it does not, every number this project produces
means nothing. The letter to send them is written (`docs/annotation_brief.md`) with one blank
left: what the annotator gets out of it — co-authorship, a credit, payment, a favour owed.
That blank is the main reason people decline when it is left vague.

---

## State

**Built and verified**

- **Phase 1 — variant set.** 1,000 variants / 448 genes, GRCh38 SNVs at >=2 review stars,
  500 pathogenic / 500 benign, gene-disjoint splits (train 600 / dev 150 / test 250), label
  balance within 1% per fold, every variant paired with a same-gene same-label partner. Plus
  a 295,819-variant VUS pool for secondary analysis.
- **Phase 3 — evidence layer.** PubTator3 client (live-verified), submitter-comment loader,
  leak sanitizer (30.8% of sentences dropped), MinHash dedup, BM25, evidence budget, and a
  retrieval gold set from expert citations. Dev-split pools built (150 variants).
- **Phase 4 — generation.** Four arms, structural invariance checked on every run,
  label-matched distractors, Claude + stub backends, recoverable batch submission.
- **Phase 5 — faithfulness metric.** Claim decomposition (rule-based + LLM), three-label
  verification with conflation detection, paired bootstrap over variants, human-validation
  harness.

**Not started**

- Phase 2 (variant scorer) — deliberately off the critical path; see protocol §2.
- Phases 6–8 (full experiment, taxonomy, writing).

**In flight**

- **Dev-split generation**, submitted 2026-10-02 as batch
  `msgbatch_012uvn6znFLxzE5e1tDybuTV` — 590 requests on `claude-opus-5`, ~$11.44, expires
  2026-10-03 18:20 UTC. Ids recorded in `results/batches.jsonl`. Collect with:
  `python scripts/06_generate.py --collect msgbatch_012uvn6znFLxzE5e1tDybuTV --split dev --backend claude --model claude-opus-5`
- **Judging of the dev split is deliberately NOT submitted** until the judge is validated.

**Spend to date:** ~$1.32 pilot generation, ~$4–6 pilot judging (estimated — predates usage
capture), ~$11.44 dev generation. Roughly $17–19 total.

---

## Secrets — read before touching git

`key.env` holds the Anthropic API key. **This repo is public.**

- `.gitignore` covers `.env`, `*.env`, `.env.*`, `key.env`, `*.key`, `secrets.*`, plus
  `annotation/` (the sheets include `_key.csv`, the automatic labels).
- A **local pre-commit hook** (`.git/hooks/pre-commit`) blocks secret-looking filenames and
  staged `sk-ant-…` / `ghp_…` / `AKIA…` content. Verified by attempting `git add -f key.env`.
- Hooks are **not** pushed. On a fresh clone the hook must be recreated or the protection is
  gone.
- Audited: `key.env` is untracked, in no commit on any branch, and no real key material
  appears in the pushed history.

Near miss worth remembering: `.gitignore` originally had only `.env`, which does **not** match
`key.env`. One `git add -A` would have published the key.

The key has already been rotated once mid-project; a 401 on every call usually means it
expired, not that the code broke.

---

## Next session — in this order

1. **Collect the dev-split batch** (command above) once it finishes.
2. **Send `docs/annotation_brief.md` to two people.** Fill the compensation blank first. Give
   each `annotator_a.csv` / `annotator_b.csv` and `ANNOTATION_GUIDE.md` from `annotation/`.
   **Never send `_key.csv`.** Score on return:
   `python scripts/08_sample_for_annotation.py --score --sheet-a annotation/annotator_a.csv --sheet-b annotation/annotator_b.csv`
3. **Read the kappa before spending anything more.** If humans cannot agree, the construct is
   underspecified and no judge can be validated against it — a result about the task that
   changes the paper, not something to tune away.
4. **Judge the dev split** (~$50 batch) — only after step 3 says the judge is trustworthy.
5. **Add a second generator family.** Every verdict so far is `same_family_as_generator=True`
   (Opus judged Opus), so self-preference is 100% of the validation sample rather than the
   intended 30% over-sample. Until non-Claude generations exist, that confound cannot be
   measured at all.
6. **Revisit the conflation detector.** It fired on ~5 claims in the pilot. With 216/259
   claims unsupported there was little supported material in which conflation *could* appear
   — decide whether that is genuine rarity or a detector too strict to be useful. It is the
   strongest proposed finding, so decide deliberately.

## Cost model — measured, corrected twice

| | standard | batch (50%) |
|---|---|---|
| generation, 590 calls (dev) | $22.89 | $11.44 |
| judging, ~4,494 claims (dev) | $101.12 | **$50.56** |
| **dev total** | **$124.01** | **$62.01** |

Two corrections already made: output runs ~1,178 tokens/call not the 450 first assumed, and
an earlier "~$12 for full dev" counted **generation only**, omitting judging — the expensive
half, because every claim carries the whole evidence pool (~2,000 input tokens). Serial
judging of the dev split takes ~4.6 hours; use `--batch` for bulk. Full study (1,000 variants
× 4 arms × 2 models) is ~$310 batch, not the $148 originally estimated.

## Rebuild from scratch

```bash
python scripts/01_build_variant_set.py --config configs/data.yaml --skip-download  # ~2 min
python scripts/03_build_clinvar_text.py                                            # ~3 min
python scripts/04_build_evidence_pools.py --split dev                              # rate limited
python scripts/06_generate.py --split dev --dry-run                                # free
pytest tests -q                                                                    # 115 tests
```
Both ClinVar dumps are in `data/raw/` (~830 MB, gitignored).

---

## Decisions locked

- Prompt gives the model the **ClinVar classification** and asks it to explain that. The FM
  scorer never enters the prompt, so Phase 2 is droppable under schedule pressure.
- **Four arms:** grounded / ungrounded / distractor_same_gene / distractor_other_gene.
- Eval set is **gated on evidence availability** (`require_submitter_comment: true`).
- Variants sampled as **same-gene same-label pairs** (`pair_by_gene: true`).
- Distractors are **label-matched**, drawn from the same split.
- Evidence budget: `k=10`, `max_chars=6000`, `variant_level_slots=1`.
- Bootstrap CIs resample **over variants, not claims**, paired across arms.
- **Temperature is unavailable** on current models; determinism is not claimed and replicates
  carry the reproducibility argument.
- Dense/hybrid retriever **deferred** — automated discovery reaches only 7% of expert-cited
  papers and the ceiling is structural (protocol §12).

## Needs your sign-off

- **`variant_level_slots = 1`.** Every variant has >=1 sanitized comment, so 1 is the only
  exactly-balanced value. Raising to 2 gives richer evidence but reopens a label gap
  (pathogenic average 2.67 comments vs benign 1.29). Flag: `--variant-slots`.
- **Compensation for annotators** — the blank in `docs/annotation_brief.md`.
- **No LICENSE file.** The repo is public, so default copyright applies and nobody may reuse
  the code. MIT or Apache-2.0 are the usual academic picks.

## Open risks

- **Annotators unstaffed.** The metric is the contribution and is unvalidated without two
  independent annotators (protocol §6). Everything else is downstream of this.
- **Judge self-preference unmeasured.** Opus judged Opus throughout.
- **Submitter identity still leaks label** — labs skew toward benign or pathogenic and write
  from house templates, so evidence *style* may carry signal after sanitation. Not fixed.
- **Sanitizer is rule-based.** Residual classification leakage is likely and not claimed to
  be eliminated.
- **Corpus coverage is thin.** 92% of probed variants had no variant-level passage in
  abstracts, so the corpus is dominated by submitter comments — the one source that states
  the answer.
- **Temporal holdout cutoff is still `null`** in `configs/data.yaml`. Dates run to 2026-08-27
  so it is viable, but it only bites if generator training cutoffs are genuinely known.

## Gotchas that cost time once already

- PubTator3's export nests under a `PubTator3` key, not `documents`, and **ignores
  `full=false`** for PMC open-access articles — 41% of a raw pool was reference-list entries.
  `prepare_pool()` filters them; never index a raw fetch.
- `submission_summary.txt.gz` has a prose line beginning `#VariationID:` before the real
  tab-separated header. Match on `#VariationID\t`.
- The variant-level reserve must run over the **whole** ranked pool. From a top-n slice it
  starved 4 of 30 dev variants, all benign.
- Truncating the variant list by row splits same-gene pairs and silently shrinks the
  `distractor_same_gene` arm. Truncate by whole (gene, label) groups.
- ClinVar stores rsIDs bare (`1952025232`) and uses `-1` for none; normalized to `rs…`.
- The lexical verifier is **degenerate on real text** (max overlap 0.471 vs a 0.50 threshold).
  Never report its output; `scripts/07` prints a DEGENERATE banner.
- Errored judge verdicts are **excluded** from rates, never counted as `unsupported`.
- Don't patch Python files with bash heredocs containing `\n` escapes — it mangles them into
  real newlines and breaks the file. Use the editor instead.
