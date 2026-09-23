# Related work — Phase 0 reading notes

## 1. Li, Li, Lavallee, Saparov, **Zitnik**, Cassa — *From Text to Translation: Using Language Models to Prioritize Variants for Clinical Review*
medRxiv 10.1101/2024.12.31.24319792 (Dec 2024); published May 2026, PMC13352863.

**What it does.** Extracts 2,391,945 free-text submission summaries from the **ClinVar XML
release** (accessed July 2025), strips non-evidence sentences, and fine-tunes BioBERT into
**ClinVar-BERT**, which detects evidence of pathogenicity/benignity inside a submission
summary. Applied to VUS, it prioritizes 7,644 variants with >=2 summaries predicted to carry
pathogenic evidence and 7,042 with benign evidence — an average of 143 variants per VCEP
(range 8–907). AUROC 0.927 for damaging vs. function-retaining.

**Why it matters to us — four concrete consequences.**

1. **ClinVar submitter summaries leak the label.** They found that description and conclusion
   sentences act as "a clear proxy for a class label", and trained a `SentenceClassifier` to
   remove them — **~25% of the text**. Our locked prompt hands the model the ClinVar
   classification *and* our Phase 3 corpus includes submitter comments. Unsanitized, the
   `grounded` arm is partly reading the answer back to us.
   → **Sanitization is a requirement, not an ablation.** The "sanitized vs. unsanitized"
   comparison in Phase 6 stays, but as a *quantification of the leak*, not as an optional arm.

2. **Template bias.** Summaries are increasingly lab-generated from standardized templates,
   giving high structural similarity. They deduplicated with **MinHash at >95% similarity**.
   For us this is worse than for them: a claim scored `supported` by six near-identical
   boilerplate sentences is not six-fold supported, and retrieval depth `k` becomes
   meaningless if the pool is full of duplicates. → Dedupe the evidence pool the same way,
   before retrieval, and report pool size after dedup.

3. **Validation circularity.** They flag "potential circularity in using functional or
   computational scores for validation if that information might be included in submission
   summaries." Our version: if the evidence pool contains summaries that themselves cite
   AlphaMissense or functional screens, then a claim marked `supported` may be supported by
   the very source we would otherwise treat as independent. → Record, per supporting passage,
   whether it is primary literature or a curated summary.

4. **Their split is weaker than ours, and that is a defensible differentiator.** They use an
   80/20 split *stratified* by class, submitting lab and gene — stratified, **not
   gene-disjoint**, and not temporal. They also apply **no review-status/star filter**. Our
   >=2-star filter and gene-disjoint split are a legitimate methodological contrast to draw.

**Their stated limitations worth inheriting:** multiple unharmonized summaries per variant;
template overfitting; residual label leakage after sentence stripping; predictions made at
submission level with no aggregation across submissions; 9,300 variants (0.5%) with
conflicting predictions across submissions.

**Pipeline note:** the free text is **not in `variant_summary.txt`**. It lives in the ClinVar
XML release / `submission_summary.txt.gz`. Phase 3 needs a second download.

---

## 2. Saadat & Fellay — *Large Language Models for Variant-Centric Functional Evidence Mining*
arXiv 2604.00075 (Mar 2026).

Benchmarks gpt-4o-mini and o4-mini on abstract screening and full-text evidence extraction
against ClinGen curated annotations; `AcmGENTIC` retrieves literature (via LitVar2), filters
abstracts, and generates evidence reports for human review.

**Scoop check: no.** It is retrieval-grounded and uses an LLM-as-judge protocol against
expert curator comments, but it evaluates **task performance** (accuracy / recall / F1). It
proposes **no claim-level faithfulness metric and no hallucination taxonomy**. It is the
closest adjacent system and belongs in our related-work section as the strongest
"grounded pipeline, unmeasured faithfulness" citation — which is precisely the gap we fill.

Also note it uses **LitVar2** for variant-level literature retrieval. Worth comparing against
PubTator3 for Phase 3 recall@k; they are NCBI siblings and LitVar is variant-centric by design.

---

## 3. Others surfaced, not yet read
- Changalidis et al., *A systematic review on generative AI applications in human medical
  genetics*, Jan 2026, PMC12863965 — 195 studies; use to position, and to check we have not
  missed a faithfulness paper.
- Wu et al., *VUS.Life: semantic embedding of variant effect annotations*, May 2026,
  PMC13421479 — embedding-based pathogenicity prediction; relevant to Phase 2 baselines only.
- *CGBench: Benchmarking Language Model Scientific Reasoning for Clinical Genetics Research*,
  arXiv 2510.11985 — unread; benchmark, check for overlap with our eval design.
