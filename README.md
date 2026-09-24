# clinrag

**Does retrieval grounding actually reduce hallucination in LLM explanations of genomic
variants — and what kind of hallucination survives it?**

Research code for an in-progress study. The intended contribution is a **claim-level
faithfulness metric** for variant explanations, validated against human annotation, plus a
**taxonomy of the hallucinations that grounding fails to prevent**. The variant scorer and the
retriever are components, not claims.

> **Status: work in progress.** The data, evidence and generation layers are built and
> verified against live sources. The faithfulness metric — the actual contribution — is not
> written yet. **No model has been run; there are no results.** Numbers below describe the
> pipeline and the corpus, not model behaviour.

---

## The question

A clinical variant classification ("this variant is pathogenic") is only useful with a
rationale. LLMs write fluent rationales and also invent them. The standard remedy is
retrieval grounding, and the standard evidence that it works is that hallucination rates go
down on some benchmark.

That answer is unsatisfying in this domain for two reasons. First, "hallucination rate" is
usually measured against a corpus, so a claim that is *true but absent from the corpus* is
scored identically to one that is *false*. In clinical genetics those are very different
failures. Second, the interesting errors are domain-specific and invisible to generic
metrics — in particular, **justifying a variant-specific claim with gene-level evidence**,
which reads as authoritative and is clinically wrong.

So the design separates three labels, not two:

| Label | Meaning | Hallucination? |
|---|---|---|
| `supported` | Entailed by a passage in the evidence pool | No |
| `unsupported` | Neither entailed nor contradicted; may well be true | Reported **separately** |
| `contradicted` | A passage contradicts it | Yes |

We measure **faithfulness to a corpus**, not correspondence to truth. Any sentence in the
write-up that says "false" where it means "unsupported" is a bug.

## Experimental design

Four arms, identical prompt structure, differing only inside the evidence block:

| Arm | Evidence supplied |
|---|---|
| `grounded` | Top-k passages retrieved for this variant |
| `ungrounded` | None — parametric knowledge only |
| `distractor_same_gene` | Passages for a **different variant in the same gene** |
| `distractor_other_gene` | Passages for a variant in an unrelated gene |

`distractor_same_gene` is the sharp arm: right gene, right condition, wrong variant. If a
model conflates gene-level with variant-level evidence, this is where it shows.

Prompt invariance is **checked, not asserted** — `scripts/06_generate.py` strips the evidence
block from every prompt and verifies the remainder is byte-identical across arms.

## Status by phase

| Phase | What | State |
|---|---|---|
| 1 | ClinVar variant set, gene-disjoint splits | done |
| 2 | Variant scorer + CADD/REVEL/AlphaMissense baselines | not started (deliberately off the critical path) |
| 3 | Evidence corpus, sanitation, retrieval | done; dense retriever deferred by decision |
| 4 | Four-arm generation | built, dry-run verified, never run against a live model |
| 5 | **Faithfulness metric** — the contribution | not started |
| 6–7 | Main experiment, ablations, taxonomy | not started |

71 tests pass (`pytest tests -q`).

## What the corpus work turned up

These are findings about the data and the tooling, measured while building. They are the
reason several design decisions look the way they do.

**Evidence availability is confounded with the label.** In ClinVar's ≥2-star pool, **95.6% of
pathogenic variants carry a usable submitter comment against 24.4% of benign ones**
(59,292/62,053 vs 69,187/283,525). The *presence* of evidence predicts the label, and
sentence-level sanitation cannot touch it. Ungated, a grounded arm would look more faithful
for reasons unrelated to grounding. The eval set is therefore gated on evidence availability,
at the cost of representing better-studied variants — a stated limitation.

**Published abstracts are not the variant-level evidence source.** Over 25 probed variants,
**23 had zero passages naming their specific variant** in titles or abstracts, against a median
pool of 51 gene-level abstracts. The per-gene cap that protects against gene-prior
memorisation also selects variants nobody has published on; those goals are in tension.

**Automated literature discovery cannot reach expert-cited evidence.** Scored against PMIDs
cited by ClinVar submitters — expert relevance judgments, independent of our retriever —
**pool recall was 0.074** (preliminary, n=15 variants). The cited papers *are* indexed in
PubTator3 and *are* annotated to the right gene, but its search does not return them at depth
250, and older papers carry no rsID annotations at all, so variant-level linking to them is
impossible in principle. LitVar2 did no better. Any grounded pipeline built on entity-linked
retrieval inherits this ceiling.

**Submitter comments leak the classification.** Consistent with
[Li et al. 2026](https://europepmc.org/article/MED/PMC13352863) (ClinVar-BERT), the free-text
comments state the answer outright. Our rule-based sanitizer removes **30.8%** of sentences;
they removed ~25% with a trained classifier. Sanitation is a *requirement* here, not an
ablation — the prompt already supplies the classification.

## Design decisions a reviewer would ask about

- **Gene-disjoint splits, never random.** ClinVar ascertainment is gene-driven, so a random
  split lets a model score well on memorised gene priors. Balancing fold *size* alone still
  left train at 54.5% pathogenic vs dev at 40.7%, because genes are internally
  label-correlated; the splitter now holds a quota per (fold, label) and fails above a 5%
  prevalence spread.
- **Paired sampling.** Variants are drawn as same-gene same-label pairs so every variant has a
  partner to borrow distractor evidence from. Without it, only 491/1000 had one.
- **Label-matched distractors.** An opposite-label distractor differs in relevance *and*
  direction of evidence, confounding "ignores irrelevant evidence" with "is swayed by contrary
  evidence".
- **An evidence budget.** Pathogenic variants carried 3.3× more evidence text. A character cap
  plus one reserved-and-capped variant-level slot brings delivered evidence to a 1.0 character
  ratio and 1.00 variant-level passages for both labels, re-verified on every build.
- **VUS excluded** from the main experiment — no ground truth to be faithful to. Kept in a
  295,819-variant pool for secondary analysis.
- **No temperature.** Current Claude models reject `temperature`/`top_p`/`top_k`. Determinism
  is not claimed; replicates carry the reproducibility argument instead.

Full reasoning, with the measurements that forced each choice, is in
[docs/eval_protocol.md](docs/eval_protocol.md). It is the binding specification —
[docs/PLAN.md](docs/PLAN.md) is the schedule, [docs/NOTES.md](docs/NOTES.md) is the current
working state.

## Repository layout

```
configs/data.yaml              experiment configuration
docs/PLAN.md                   14-week phase plan
docs/eval_protocol.md          binding protocol: locked decisions + the evidence for them
docs/related_work.md           prior work and what it changes here
docs/NOTES.md                  cold-start handoff: state, next steps, known gotchas
scripts/
  01_build_variant_set.py      ClinVar -> labelled, gene-split, evidence-gated set
  02_probe_corpus_coverage.py  how much variant-level evidence exists per variant
  03_build_clinvar_text.py     submitter comments -> sanitized, deduplicated
  04_build_evidence_pools.py   both sources -> ranked pool + evidence budget report
  05_eval_retrieval.py         retrieval vs the expert-citation gold set
  06_generate.py               four-arm generation (--dry-run costs nothing)
src/clinrag/
  data/                        variant set construction, gene-level splitting
  retrieval/                   PubTator3 client, sanitation, BM25, gold set
  generation/                  prompts, distractors, model backends
  metric/                      Phase 5 — not yet implemented
  analysis/                    Phases 6-7 — not yet implemented
tests/                         71 tests
```

`data/` and `results/` are gitignored. The ClinVar dumps are ~830 MB and every derived
artefact is reproducible from the scripts.

## Reproducing

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev,retrieval]"
```

```bash
# Phase 1 — variant set (downloads ~830 MB of ClinVar dumps on first run)
python scripts/01_build_variant_set.py --config configs/data.yaml

# Phase 3 — evidence
python scripts/03_build_clinvar_text.py
python scripts/04_build_evidence_pools.py --split dev     # rate-limited, cached per variant
python scripts/05_eval_retrieval.py --split dev --cached-only

# Phase 4 — generation. Dry run builds every prompt and spends nothing.
python scripts/06_generate.py --split dev --dry-run

pytest tests -q
```

Live generation needs `pip install anthropic` and credentials (`ANTHROPIC_API_KEY`, or
`ant auth login`). It costs money: the full design is 4 arms × 2 models × |variants|, so 8,000
generations per model at n=1000. Generation goes through the Batch API at 50% cost via
`--batch`. **Dry-run and pilot on a handful of variants before committing to a full run.**

## Data sources

| Source | Use |
|---|---|
| [ClinVar `variant_summary`](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/) | variant set, classifications, review status |
| [ClinVar `submission_summary`](https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/) | submitter free-text comments (the variant-level evidence) |
| [PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/) | entity-annotated abstracts (gene-level context) |

## Known limitations

- Faithfulness is measured **to a corpus**, not to truth.
- The eval set is gated on evidence availability, so it represents **better-studied variants**.
- The sanitizer is rule-based; residual classification leakage is likely and is not claimed to
  be eliminated.
- **Submitter identity still correlates with label** (different labs skew toward benign vs
  pathogenic submissions, each with house templates), so evidence *style* may carry label
  signal after sanitation. Not fixed.
- The retrieval gold set rewards finding papers curators already found — a lower bound on
  recall, not a ceiling on quality — and is 95% pathogenic, so it cannot compare retrieval
  across labels.
- The memorisation confound is mitigated by a temporal holdout, not eliminated.

## Related work

- Li, Li, Lavallee, Saparov, Zitnik, Cassa. *From Text to Translation: Using Language Models to
  Prioritize Variants for Clinical Review.* medRxiv 2024; published 2026.
  [PMC13352863](https://europepmc.org/article/MED/PMC13352863) — ClinVar-BERT; the source of
  the label-leakage and template-bias findings this repo builds on.
- Saadat & Fellay. *Large Language Models for Variant-Centric Functional Evidence Mining.*
  [arXiv:2604.00075](https://arxiv.org/abs/2604.00075) — closest adjacent system: grounded
  pipeline with LLM-as-judge, but no claim-level faithfulness metric and no taxonomy.

## License

This project is licensed under the MIT License!
