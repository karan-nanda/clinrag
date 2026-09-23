# Working notes — pick up here

Last worked: 2026-09-23. Phases 1, 3 and 4 are built. 66 tests pass. Nothing is committed.

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

## Next session — in this order

1. **Set up API credentials** (`ant auth login`, or export `ANTHROPIC_API_KEY`). `anthropic`
   1.8.0 is installed; nothing has ever been called.
2. **Dry-run generation and read the prompts yourself** — this spends nothing and is the last
   cheap moment to catch a prompt problem:
   `python scripts/06_generate.py --split dev --dry-run`
3. **Small paid pilot** before the full run — e.g. `--n 10 --backend claude` on dev, read the
   outputs, confirm they look like real rationales and not list-shaped boilerplate.
4. **Full pool build**, ~1 hour rate-limited, cached per variant:
   `python scripts/04_build_evidence_pools.py --split all --k 10 --max-chars 6000 --variant-slots 1`
   Re-check `results/evidence_budget.md` — a char ratio above 1.15 is blocking.
5. **Phase 5 — the faithfulness metric.** This is the contribution and the longest phase.
   Structured outputs (`output_config.format`) are the right tool for claim decomposition and
   the judge; `client.messages.parse()` validates against a schema.

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
