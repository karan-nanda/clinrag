# Evaluation protocol

**Status: DRAFT — Phase 0 deliverable. Must be frozen before Phase 4 generation runs.**
Nothing downstream should be coded against a decision still marked `TBD`.

---

## 1. Definition of hallucination (the one we defend)

> **TBD — write this first.**

The definition must make this distinction explicit, because the whole Phase 5 metric and
Phase 7 taxonomy rest on it:

| Label | Meaning | Counts as hallucination? |
|---|---|---|
| `supported` | Claim entailed by >=1 passage in the evidence pool | No |
| `unsupported` | No passage entails or contradicts it; may still be true | **Reported separately** |
| `contradicted` | A passage contradicts the claim | Yes |

We measure **faithfulness to the evidence corpus**, not correspondence to truth. Any sentence
in the paper that says "false" where it means "unsupported" is a bug.

Open question for Phase 0: does an *unverifiable-in-principle* claim (hedging, boilerplate,
"further study is warranted") get a fourth label, or get dropped before scoring?

## 2. What the explanation explains — **LOCKED**

The prompt gives the variant and its **ClinVar classification**, and asks the model to explain
why the variant carries that classification.

Consequences, deliberately chosen:
- The FM scorer and CADD / REVEL / AlphaMissense are a **separate classification baseline**.
  Their outputs never enter the generation prompt.
- Phase 2 therefore runs *beside* Phase 4, not upstream of it, and is droppable under schedule
  pressure without invalidating the main result.
- We avoid the confound where a model hallucinates in order to rationalise a wrong prediction.

## 3. Frozen choices

| Decision | Value | Locked? |
|---|---|---|
| Prompt target | ClinVar classification (see §2) | **yes** |
| Arms | grounded / ungrounded / distractor-same-gene / distractor-other-gene | **yes** |
| Genomic FM | Nucleotide Transformer v2 250M | TBD |
| Sequence window | TBD (bp around the variant) | TBD |
| Generator A (open-weight) | TBD | TBD |
| Generator B (API) | TBD | TBD |
| Corpus sources | PubTator3 abstracts + ClinVar submitter comments + GeneReviews | TBD |
| Retriever | BM25 -> dense (PubMedBERT / SPECTER2) -> hybrid | TBD |
| Claim decomposer | TBD | TBD |
| Verifier | NLI model + LLM judge | TBD |
| Temporal holdout cutoff | TBD | TBD |

## 4. Experimental arms (Phase 4) — **LOCKED**

| Arm | Evidence block contains |
|---|---|
| `grounded` | Top-k passages retrieved for this variant |
| `ungrounded` | Nothing (parametric knowledge only) |
| `distractor_same_gene` | Top-k passages retrieved for a **different variant in the same gene** |
| `distractor_other_gene` | Top-k passages retrieved for a variant in an **unrelated gene** |

The two distractor sub-arms separate two different failures:
- `distractor_other_gene` — does the model notice irrelevant evidence at all?
- `distractor_same_gene` — does the model **conflate gene-level with variant-level evidence**?
  This is the clinically significant taxonomy category and the sharper of the two.

A same-gene distractor may make the grounded pipeline look *worse* than ungrounded. That is a
real result, not a bug; report it as one.

Prompt structure is held constant across arms; only the evidence block changes. Temperature 0
for the main run, plus N=TBD sampled generations per arm for variance.

Cost note: four arms x two models x |variants| generations. Budget before, not after.

## 5. Units of analysis

- Unit of generation: one variant, one arm, one model.
- Unit of scoring: one **atomic claim**.
- Primary outcomes: per-explanation **contradicted-claim rate** and **unsupported-claim rate**.
- Uncertainty: bootstrap CIs **resampled over variants, not over claims** — claims within an
  explanation are not independent, and resampling claims gives intervals that are too tight.

## 6. Metric validation (Phase 5, non-negotiable)

- 150–300 claims, stratified across arms and models.
- Two annotators, independently, against the same evidence pool.
- Report inter-annotator agreement (Cohen's kappa) and automatic-vs-human agreement, per label.
- Adjudication procedure for disagreements: TBD.
- **Open staffing question:** who is the second annotator? This is the single largest schedule
  risk in the plan — the metric is the contribution, and it is unvalidated without them.

## 7. Splits and leakage

- Gene-level split; no gene appears in two folds. ClinVar ascertainment bias is gene-level, so
  random splits are memorizable.
- Temporal holdout: variants whose ClinVar `LastEvaluated` postdates the cutoff.
  Caveat to state plainly: this only works if the generators' training cutoffs are actually
  known, and retrieved abstracts for a held-out variant may still predate it.
- The memorization confound is a stated limitation, not something we claim to eliminate.

## 8. Retrieval evaluation (Phase 3, standalone)

Small gold set of variant -> relevant-passage pairs; report recall@k. Required so a weak
grounded arm can be attributed to retrieval failure vs. generation failure.

## 9. Corpus hygiene — **required**, derived from related work

Consequences of Li et al. 2026 (ClinVar-BERT); see `docs/related_work.md` §1.

1. **Sanitize ClinVar submitter comments before they enter any evidence pool.** Description
   and conclusion sentences are a direct proxy for the classification, and our prompt already
   supplies the classification. Remove them; expect to drop ~25% of the text. Report the
   residual-leakage risk rather than claiming it is eliminated.
2. **Deduplicate the evidence pool** (MinHash, >95% similarity) before retrieval. Lab
   templates produce near-identical summaries; without dedup, `supported` counts and
   retrieval depth `k` are both inflated by boilerplate. Report pool size pre- and post-dedup.
3. **Tag each supporting passage** as primary literature vs. curated summary. A claim
   supported only by curated summaries that themselves cite AlphaMissense is not independently
   supported — this is the circularity Li et al. flag, in our setting.
4. **Corpus source note:** submitter free text is not in `variant_summary.txt`; it requires the
   ClinVar XML release or `submission_summary.txt.gz`. Phase 3 needs that second download.

## 10. Evidence-availability confound — **measured, and it bites**

Measured on the 2-star ClinVar pool (GRCh38 SNVs, >=2 stars):

| | benign | pathogenic |
|---|---|---|
| variants with a usable submitter comment | 69,187 / 283,525 (**24.4%**) | 59,292 / 62,053 (**95.6%**) |

On the first 1,000-variant build this reproduced per split: pathogenic 94–99% covered,
benign 19–26%. **The mere presence of evidence predicts the label.** Sentence-level
sanitation cannot touch this; the leak is structural, not textual. Ungated, the `grounded`
arm would receive rich evidence for pathogenic variants and near-nothing for benign ones, and
any faithfulness gain would be unattributable.

**Decision (implemented):** `filters.require_submitter_comment: true` restricts the eval set
to variants carrying >=1 comment that survives sanitation. After this, coverage is 100% in
both classes across all folds. Headroom is ample — 69k benign and 59k pathogenic remain, and
we sample 500 each.

**Cost, which goes in the limitations:** the eval set now represents *better-studied*
variants, not variants in general. State it plainly.

### Residual confounds — not yet fixed

1. **Evidence volume.** Pathogenic variants still carry more: mean 2.67 comments / 1,602
   sanitized chars vs benign 1.29 / 484 (3.3x). A fixed retrieval depth `k` and a fixed
   character budget in the prompt largely equalize this — **set both, and verify** that
   delivered evidence length does not differ by label.
2. **Submitter identity.** Benign comments come mostly from Illumina and Myriad; pathogenic
   from Labcorp/Invitae, GeneDx and Ambry. Labs write from distinctive templates, so evidence
   *style* carries label signal even after the text is sanitized. Report the submitter
   distribution per arm; consider submitter as a covariate, or match on it.

### Corpus coverage: PubMed abstracts are not the primary source

Probe over 25 dev variants (`scripts/02_probe_corpus_coverage.py`, PubTator3, 3 search pages):

- **23/25 (92%) had zero variant-level passages** in titles+abstracts; 22/25 with body text.
- Median pool: 51 abstracts per variant — but essentially all **gene-level**.

So for most variants the literature offers gene-level context only, while ClinVar submitter
comments (100% coverage by construction) carry the variant-level evidence. Two consequences:

- The evidence pool is dominated by the one source that states the answer, so sanitation
  quality is not a detail — the validity of the whole comparison rests on it. Current
  rule-based drop rate is **30.8%**, against the ~25% Li et al. removed with a trained
  classifier. Upgrade to a trained classifier if the human validation in Phase 5 shows leaks.
- `distractor_same_gene` needs care: if real evidence is gene-level anyway, a same-gene
  distractor may be nearly indistinguishable from it. That is measurable — tag every
  delivered passage `variant`/`gene`/`other` and report the mix per arm.

## 11. Retrieval — measured behaviour, and the evidence budget

### BM25 is weak on this corpus, for a structural reason

Our pools are already gene-filtered, so the gene symbol appears in nearly every document and
its IDF collapses toward zero. In the degenerate case — every query term present in half the
corpus — `BM25Okapi` returns an IDF of exactly 0 for all terms and **every document scores
0**, leaving ranking to insertion order.

Consequences, all verified on dev data:

- Ranking is driven by variant-specific tokens (rsID, HGVS), not the gene symbol.
- Sanitized submitter comments say "this variant" rather than repeating the symbol, so they
  score near zero and were ranking **below 30 gene-level abstracts**.
- Ties are now broken by `(level, doc_id)`, variant-level first. This makes ranking
  reproducible, which temperature-0 generation requires.

This is a finding worth reporting, not just an implementation note: on a variant-level corpus,
lexical retrieval's strongest signal is the identifier, and the plan's dense/hybrid retriever
has a clear job — matching evidence that never names the variant explicitly.

### The evidence budget — **locked**

Every prompt receives at most `k` passages within `max_chars`, with `variant_level_slots`
passages reserved for and capped at variant-level evidence.

| Parameter | Value | Reason |
|---|---|---|
| `k` | 10 | plan default; retrieval-depth ablation varies it |
| `max_chars` | 6000 | closes the 3.3x length cue in §10 |
| `variant_level_slots` | 1 | every variant has >=1 sanitized comment, so 1 is exactly balanced |

The reserve must be applied over the **whole ranked pool**, never a top-n slice. Reserving
from BM25's top-30 starved the slot on 4 of 30 dev variants, all benign — which would have
re-created the label confound §10 exists to remove.

Measured on 30 dev variants after the fix:

| | benign | pathogenic |
|---|---|---|
| mean delivered passages | 7.64 | 7.81 |
| mean delivered characters | 5,643 | 5,576 |
| mean variant-level passages | **1.00** | **1.00** |

Character ratio 1.01. Both confounds from §10 are closed at this setting. `budget_report()`
re-checks this on every build; **re-run it before any generation run**, and treat a ratio
above 1.15 as blocking.

### Still open in Phase 3

- Dense retriever (PubMedBERT / SPECTER2) and the hybrid, with BM25 as the baseline.
- The recall@k gold set. `recall_at_k()` exists; the labelled variant->passage pairs do not.
  Without them, a weak `grounded` arm cannot be attributed to retrieval vs. generation.
- Full 1,000-variant pool build (~1 hour, rate limited; cached per variant).

## 12. Retrieval evaluation, and a structural limit on literature grounding

### The gold set

PMIDs cited by ClinVar submitters for a specific variant are expert relevance judgments,
produced independently of our retriever and free. Extracted from **raw** descriptions (safe:
gold labels only score retrieval, they never enter a prompt). Coverage: 486/1000 variants,
2,151 distinct PMIDs, median 2 per covered variant. See `src/clinrag/retrieval/goldset.py`.

### Result: automated discovery cannot reach expert-cited evidence

Measured on cached dev variants (`scripts/05_eval_retrieval.py`):

- **pool_recall = 0.074.** 93% of expert-cited papers are not in the candidate pool at all.
- Of the reachable remainder, recall@10 = 0.333.

Diagnosis, verified rather than assumed:

- The cited papers **are** indexed in PubTator3 and **are** annotated to the right gene.
- PubTator3 search does not return them at depth 250 (`@GENE_NF2`, 250 results, zero hits).
  Its ranking favours recent and topically central work; curators cite foundational papers,
  often from the 1980s–1990s.
- Those older papers carry **no rsID annotations at all**, so variant-level entity linking to
  them is impossible in principle, not merely missed.
- LitVar2 (the variant-centric sibling used by Saadat & Fellay) did no better: 0–1 gold hits
  per variant, and HTTP 400 on several rsIDs.

This is a reportable finding, not only an engineering setback: **in clinical genetics, the
evidence experts actually cite is largely unreachable by current automated variant-literature
linking.** Any grounded pipeline built on entity-linked retrieval inherits this ceiling.

### The asymmetry that constrains the whole design

| | benign | pathogenic |
|---|---|---|
| variants with >=1 expert-cited paper | 24/500 (**4.8%**) | 462/500 (**92.4%**) |
| mean cited papers per variant | 0.32 | 4.78 |

This is a fact about the domain, not about our pipeline: pathogenicity is argued from
published case and functional data, while benignity is argued from population allele
frequency, which cites nothing. **Literature grounding is intrinsically asymmetric here.**

Consequence: pulling curator-cited papers into the evidence pool would rebuild the exact
label confound that §10 exists to remove, and would do so through a channel sanitation cannot
touch. The current design avoids this by using sanitized submitter comments as the balanced
variant-level source (100% coverage in both classes, 1 reserved slot each) and treating
literature as gene-level context.

### Decisions taken on this evidence — **LOCKED**

1. **Corpus stays balanced.** Sanitized submitter comments remain the variant-level source
   (100% coverage in both classes, one reserved slot). Curator-cited papers are **not** pulled
   into the pool, because doing so would rebuild the §10 label confound through a channel
   sanitation cannot reach. Literature remains gene-level context.
   The 7% pool_recall is reported as a **finding**, not hidden as a shortfall.
2. **Dense/hybrid retriever is deferred.** It can only move the ranking number (33% of
   reachable papers in top-10); it cannot move the corpus number (7% reachable), which is
   where the loss is. BM25 plus the reserved variant-level slot stands as the Phase 3
   retriever. Revisit only if Phase 6 shows retrieval quality is the binding constraint.
   The plan's retrieval-quality-degradation ablation still runs — degrade the pool we have.

Effect on the schedule: Phase 3 closes here and Phase 4 starts early, which buys time for
Phase 5, the contribution and the longest phase.

## 13. Generation — Phase 4 settings

### Temperature 0 is not available. The plan's line must change.

`temperature`, `top_p` and `top_k` were **removed** on Claude Opus 5, Sonnet 5, Opus 4.7/4.8
and Fable; sending any of them returns a 400. Only older models (Sonnet 4.6, Haiku 4.5) still
accept them. "Temperature 0 for the main run" is therefore not implementable on the API arm.

What we do instead, and what the paper must say:

- Fix `output_config.effort` (default `medium`) and record model, effort and `max_tokens` on
  **every** generation record.
- Do not claim determinism. Run **N replicates per arm** and report run-to-run variance —
  which the plan already required for variance characterization; it is now the only source of
  the reproducibility claim, not a supplement to it.
- An open-weight generator run locally **can** set temperature 0. The two generators therefore
  differ in determinism. State this asymmetry rather than implying a common setting.
- `thinking` is adaptive by default on Opus 5. The API model reasons before answering; a local
  open-weight model does not. This is a model difference, which is precisely why the plan uses
  two generators — but it must not be described as a controlled variable.

### Cost control

The main run is 4 arms x 2 models x |variants|; at 1,000 variants, 8,000 generations per
model. Generation goes through the **Batch API at 50% cost** (`--batch`). Batch results
return in arbitrary order and are keyed by `custom_id`, never by position.

Prompt caching is available but of limited use here: the shared prefix (system + variant
header) is well under the minimum cacheable prefix, and the evidence block — the bulk of the
tokens — differs per variant.

### Paired sampling — **LOCKED**

`sampling.pair_by_gene: true` draws variants as same-gene, same-label **pairs**, so every
variant has a partner to borrow distractor evidence from. Before this, only 491/1000 variants
had such a partner and `distractor_same_gene` could run on half the set. Two per (gene, label)
also serves as the per-gene cap. Current build: 1,000 variants, 448 genes, max 4 variants from
any one gene, **1000/1000 with a same-gene same-label partner**, folds within 1% on label.

### Distractors are label-matched — **LOCKED**

An opposite-label distractor would differ in both relevance and direction of evidence,
confounding "ignores irrelevant evidence" with "is swayed by contrary evidence". Matching the
label leaves variant identity as the only thing wrong. Distractors are drawn from the same
split, so evidence never crosses a fold boundary.

### Prompt invariance is checked, not asserted

`scripts/06_generate.py` writes `results/prompt_audit.md`, which strips the EVIDENCE block
from every prompt and verifies the remainder is byte-identical across arms. The `ungrounded`
arm keeps the EVIDENCE header with `(none provided)` rather than dropping the section, since
removing a section changes prompt shape.

The task instruction deliberately does **not** say "use only the evidence provided" — that
would suppress unsupported claims directly, which is the quantity being measured. A test
enforces this.

### Distractor usability — measured on dev, and it constrains the analysis

Building the arms revealed two things that are invisible in the prompt and would have
silently weakened the headline comparison.

**1. Some "distractors" are not distractors.** Two variants in the same gene can retrieve the
same passages, in which case `distractor_same_gene` *is* `grounded`. Measured at **10/150**
on dev. These are dropped at prompt-build time, not averaged in: keeping them pulls the arm's
effect toward zero for a mechanical reason. The count is reported on every run.

**2. Conflation is only detectable when the evidence names a variant.** The model can notice
evidence is about the wrong variant only if the evidence identifies a variant at all. On dev,
after building `donor_keys` from rsID *and* protein *and* c. notation:

| arm | n | evidence names the donor | names some variant |
|---|---|---|---|
| `distractor_same_gene` | 140 | 62 (**44%**) | 111 (79%) |
| `distractor_other_gene` | 150 | 62 (41%) | 106 (71%) |

Where no variant is named, generic gene-level text is not detectably wrong for the target,
and the arm is not testing conflation — it is testing tolerance of vague evidence, which is a
different question.

**Analysis requirement:** Phase 6 and Phase 7 must **stratify the distractor arms by
`names_donor`**, not pool them. Every generation record carries `meta.names_donor`,
`meta.names_a_variant`, `meta.identical_to_grounded` and `meta.donor_id` for exactly this.
Pooling would dilute the conflation signal with cases where no signal can exist.
