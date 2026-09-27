# Working notes — pick up here

Last worked: 2026-09-26. Phases 1, 3, 4 and 5 built and exercised end to end against real
models. 115 tests pass. Branch `phase5-faithfulness`, pushed, in sync with origin.

**Phase 3 closed early by decision** (see eval_protocol.md §12): automated literature
discovery reaches only 7% of expert-cited papers and the ceiling is structural, so the dense
retriever was deferred and Phase 4 started instead.

Read `docs/eval_protocol.md` first — §10 and §11 hold the measured findings that constrain
everything downstream. `docs/related_work.md` §1 explains why the sanitizer exists.

---

## Where things stand

**Done and verified**

- **Phase 1 — variant set.** 1,000 variants / 598 genes, GRCh38 SNVs at >=2 review stars,
  500 pathogenic / 500 benign, gene-disjoint splits (train 600 / dev 150 / test 250), perfect
  50/50 label balance in every fold. Plus a 295,819-variant VUS pool for the secondary
  analysis. `data/processed/variants.parquet`, card at `variant_set_card.md`.
- **Phase 3 — evidence layer.** PubTator3 client (live-verified), ClinVar submitter-comment
  loader, leak sanitizer, dedup, BM25, evidence budget. Pools built for 30 dev variants.

- **Phase 4 — generation.** Four arms with verified structural invariance, label-matched
  distractors, Claude + stub backends, Batch API support. Never yet run against the real API.

**Not started**

- Phase 2 (variant scorer) — deliberately off the critical path; see protocol §2.
- Phase 5 (faithfulness metric) — the contribution. Phases 6–8.

---

## Secrets — read before touching git

`key.env` holds the Anthropic API key. **This repo is public.**

- `.gitignore` covers `.env`, `*.env`, `.env.*`, `key.env`, `*.key`, `secrets.*`.
- A **local pre-commit hook** (`.git/hooks/pre-commit`) blocks staging any secret-looking
  filename and blocks staged content matching `sk-ant-…`, `ghp_…`, `AKIA…`. Verified by
  attempting `git add -f key.env` — the commit was refused.
- Hooks are **not** pushed with the repo. On a fresh clone the hook must be recreated, or
  the protection is gone.
- Audited: `key.env` is untracked, appears in no commit on any branch, and `sk-ant-` appears
  in no committed blob anywhere.

Near miss worth remembering: `.gitignore` originally had only `.env`, which does **not**
match `key.env`. One `git add -A` would have published the key.

## Where things stand (2026-09-26)

- **Judged pilot complete.** 34 generations -> 259 claims -> 259 judged verdicts, 0 errors.
  Results and caveats in `docs/pilot_results.md`. Directionally what the study predicts;
  statistically meaningless at n=6-10 paired variants.
- **Human validation is staffed-pending.** Calibration sheets are generated and waiting in
  `annotation/` (gitignored). `docs/annotation_brief.md` is ready to send — it has ONE blank
  to fill before sending: what the annotator gets (co-authorship / acknowledgement / payment).
  Nobody has been asked yet. **This is the critical path.**
- **Nothing beyond the pilot has been spent.** Total spend to date: ~$1.32 generation plus
  ~$4-6 judging (the judged run predates usage capture, so it is an estimate).

### Cost model — corrected twice, trust these numbers

Measured from real runs, not assumed:

| | standard | batch (50%) |
|---|---|---|
| generation, 590 calls (full dev) | $22.89 | $11.44 |
| judging, ~4,494 claims (full dev) | $101.12 | **$50.56** |
| **full dev total** | **$124.01** | **$62.01** |

Two corrections already made here: output is ~1,178 tokens/call not the 450 first assumed,
and an earlier "~$12 for full dev" counted **generation only** and omitted judging, which is
the expensive half. **Judging dominates** because every claim carries the whole evidence pool
(~2,000 input tokens each). Serial judging of the full dev split takes ~4.6 hours at the
pilot's observed rate; use `--batch` for bulk.

### Open sequencing decision

Judging the full dev split costs ~$50 while the judge is **not yet human-validated**.
Generations (~$11 batch) are reusable whatever validation shows; judging is not. The
conservative order is: generate now, hold judging until the annotators return. Not yet
decided.

## Pilot run — 2026-09-23 (first real generations)

10 variants x 4 arms on `claude-opus-5`, effort=medium, 34 calls, 0 errors, **$1.32**.
Outputs in `results/generations_pilot.jsonl` (gitignored).

**Quality is good.** Real clinical rationales, not boilerplate. The grounded arm demonstrably
uses the evidence — one explanation lifted "ostensibly healthy adult undergoing
carrier/predisposition screening" straight from the ICSL submitter comment.

**Cost estimate corrected.** Output is ~**1,178 tokens/call**, not the 450 originally assumed.
Input (~1,868) was accurate. Revised: full dev split 600 calls = $23 standard / **$12 batch**;
full study 8,000 calls x 2 models = **~$310 batch**, not $148. Budget from these numbers.

**The lexical verifier is degenerate on real text — do not report its output.** No claim
reached the 0.50 overlap threshold (max observed **0.471**) because models paraphrase rather
than copy, so every claim scored `unsupported` in every arm. `scripts/07` now prints a
DEGENERATE banner when a verifier assigns every claim one label. The threshold must be
calibrated against human labels, **never by eye** — tuning it to produce a nicer number is
fitting the metric to the desired result.

**Worth noting for Phase 7:** the ungrounded arm asserted "The variant has been submitted to
ClinVar by multiple clinical laboratories with concordant benign/likely benign assertions."
Nothing in the prompt says that. Confident, checkable, corpus-unverifiable — exactly the
phenomenon the metric targets.

## Next session — in this order

1. **Send `docs/annotation_brief.md` to two people.** Fill the compensation blank first.
   Sheets are already generated in `annotation/`; give each annotator their own
   `annotator_a.csv` / `annotator_b.csv` and `ANNOTATION_GUIDE.md`. **Never send `_key.csv`** —
   it holds the automatic labels. ~1.5-2h each for the 50-claim calibration round.
   Score on return:
   `python scripts/08_sample_for_annotation.py --score --sheet-a annotation/annotator_a.csv --sheet-b annotation/annotator_b.csv`
2. **Read the kappa before spending anything more.** If humans cannot agree, the construct is
   underspecified and no judge can be validated against it — that is a result about the task
   and it changes the paper, not something to tune away.
3. **Generate the full dev split** (~$11 batch). Reusable regardless of validation outcome:
   `python scripts/04_build_evidence_pools.py --split all` (if pools are missing) then
   `python scripts/06_generate.py --split dev --backend claude --batch`
4. **Judge the full dev split** (~$50 batch) — only after step 2 says the judge is trustworthy.
5. **Add a second generator family.** Every pilot verdict is `same_family_as_generator=True`
   (Opus judged Opus), so self-preference is 100% of the sample rather than the intended 30%
   over-sample. Until generations exist from a non-Claude model, that confound cannot be
   measured at all.
6. **Revisit the conflation detector.** It fired on ~5 claims. With 216/259 claims unsupported
   there is little supported material in which conflation could be detected — decide whether
   that is a real rarity or a detector too strict to be useful.

## Rebuild from scratch

```powershell
python scripts/01_build_variant_set.py --config configs/data.yaml   # ~2 min (dumps cached)
python scripts/03_build_clinvar_text.py                             # ~3 min
python scripts/04_build_evidence_pools.py --split dev --n 30        # rate limited
python -m pytest tests -q                                           # 43 tests
```
Both ClinVar dumps are already in `data/raw/` (~830 MB, gitignored). Pass `--skip-download`
to script 01 to reuse them.

---

## Decisions locked

- Prompt gives the model the **ClinVar classification** and asks it to explain that. The FM
  scorer never enters the prompt, so Phase 2 is droppable under schedule pressure.
- **Four arms:** grounded / ungrounded / distractor_same_gene / distractor_other_gene.
  Cost is 4 arms x 2 models x |variants| — budget before Phase 4, not during.
- Eval set is **gated on evidence availability** (`require_submitter_comment: true`).
- Evidence budget: k=10, max_chars=6000, variant_level_slots=1.
- Bootstrap CIs resample **over variants, not claims**.

## Needs your sign-off

- **`variant_level_slots = 1`.** Every variant has >=1 sanitized comment, so 1 is the only
  exactly-balanced value. Raising to 2 gives richer evidence but reopens a label gap
  (pathogenic average 2.67 comments vs benign 1.29). Flag: `--variant-slots`.

## Open risks

- **Second annotator for Phase 5 is unstaffed.** Largest schedule risk in the plan: the
  metric is the contribution and it is unvalidated without them. 150–300 claims, two
  annotators, report agreement.
- **Submitter identity still leaks label** — Illumina/Myriad skew benign, Invitae/GeneDx/
  Ambry skew pathogenic, and labs write from house templates, so evidence *style* carries
  signal after sanitation. Not fixed. Options: report the distribution per arm, treat
  submitter as a covariate, or match on it.
- **Sanitizer is rule-based**, dropping 30.8% of sentences vs the ~25% Li et al. removed with
  a trained classifier. Close enough to proceed; upgrade if Phase 5 human validation shows
  leaks. Residual leakage is a limitation either way.
- **Corpus coverage is thin.** 92% of probed variants had no variant-level passage in
  abstracts. ClinVar comments carry the variant-level evidence, which means the corpus is
  dominated by the one source that states the answer.
- **Temporal holdout cutoff is still `null`** in `configs/data.yaml`. Dates run to 2026-08-27,
  so it is viable — but it only bites if generator training cutoffs are genuinely known.

## Gotchas that cost time once already

- PubTator3's export nests under a `PubTator3` key, not `documents`, and **ignores
  `full=false`** for PMC open-access articles — 41% of a raw pool was reference-list entries.
  `prepare_pool()` filters them; never index a raw fetch.
- `submission_summary.txt.gz` has a prose line beginning `#VariationID:` before the real
  tab-separated header. Match on `#VariationID\t`.
- The variant-level reserve must run over the **whole** ranked pool. From a top-n slice it
  starved 4 of 30 dev variants, all benign.
- ClinVar stores rsIDs bare (`1952025232`) and uses `-1` for none; normalized to `rs...` in
  `clinvar.py`.
