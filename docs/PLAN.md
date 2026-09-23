# Project plan — grounded vs. ungrounded variant explanations

Timeline: 14 weeks. Contribution is the **faithfulness metric + hallucination taxonomy**
(Phases 5 and 7); the variant scorer and retriever are components, not claims.

---

## Phase 0 — Scope and decisions (Week 1)

Read Zitnik Lab variant-prioritization work and the FActScore / SAFE line on atomic claim
verification; the metric here is a domain adaptation of that line.

Lock down and write into `docs/eval_protocol.md` **before any code**:
- which genomic FM
- which two generator models (one open-weight, one API)
- corpus sources
- the exact definition of "hallucination" we will defend

## Phase 1 — Data (Weeks 1–3)

- Pull ClinVar `variant_summary.txt`.
- Filter to review status >= 2 stars (multiple submitters, no conflicts) for label reliability.
- Balance pathogenic / benign.
- **Exclude VUS** from the main experiment; revisit as secondary analysis (no ground truth).
- **Split by gene, not randomly.** Random splits let a model memorize gene-level priors driven
  by ClinVar's ascertainment bias.
- Target 500–2,000 variants for the explanation eval. Generation cost, not data, is the bottleneck.

## Phase 2 — Variant scorer (Weeks 2–4)

- Zero-shot Nucleotide Transformer v2 (250M) scoring ref/alt sequence windows.
- Then a logistic head on frozen embeddings. No full fine-tune unless compute is free.
- Reference baselines (precomputed, downloadable): CADD, REVEL, AlphaMissense.
- Expectation: the FM classifier probably will not beat AlphaMissense. That is fine — it is a
  component, not the contribution. Do not oversell it in the paper.

## Phase 3 — Retrieval (Weeks 3–5)

- PubTator3 rather than raw PubMed: abstracts already linked to genes, variants, rsIDs
  (saves weeks of entity resolution).
- Add ClinVar submitter comments and GeneReviews.
- BM25 first (strong, cheap baseline), then dense (PubMedBERT or SPECTER2), then hybrid.
- **Evaluate retrieval on its own** with recall@k against a small gold set, so that when
  grounding underperforms we can tell whether retrieval or generation failed.

### Vertical slice checkpoint — end of Week 4
Run all four components end-to-end on 20 variants, even if every piece is bad.
Surfaces integration problems while there is still time.

## Phase 4 — Generation (Weeks 5–6)

- Three arms: **grounded**, **ungrounded**, **distractor**.
- Hold prompt structure as constant as possible across arms.
- Two generator models (one open-weight, one API) for robustness.
- Temperature 0 for the main run, plus a handful of sampled generations to characterize variance.

## Phase 5 — Faithfulness metric (Weeks 6–9)  ← core contribution, longest phase

- Decompose each explanation into atomic claims with an LLM.
- Verify each claim against the evidence pool: NLI model + LLM judge.
- Label **supported / unsupported / contradicted**.

Two things must happen or the paper does not stand up:
1. **Validate the automatic metric against human annotation** on 150–300 claims, two
   annotators, report agreement.
2. **Keep "unsupported" and "false" separate.** A claim can be true and simply absent from the
   corpus. That distinction is where the interesting findings live and it feeds the taxonomy.

## Phase 6 — Main experiment and ablations (Weeks 9–11)

- Full run across arms, models, and the temporal holdout.
- Ablations: sanitized vs. unsanitized evidence; retrieval depth (k); retrieval quality degradation.
- Bootstrap confidence intervals on the hallucination-rate difference.

## Phase 7 — Taxonomy (Weeks 10–12)

- Open-code ~200 hallucinated claims, collapse into categories.
- Second annotator applies the finalized taxonomy; report agreement.
- Starting categories (the two proposed) plus ones that show up in this domain:
  - conflating **gene-level** evidence with **variant-level** evidence  ← clinically significant;
    strong finding if frequent
  - fabricated inheritance patterns
  - invented population frequencies
  - citations to studies that do not exist

## Phase 8 — Writing (Weeks 12–14)

- Draft in parallel with Phase 6, not after it.
- Limitations must name: the memorization confound, corpus coverage, and that we measure
  **faithfulness to a corpus**, not faithfulness to truth.
