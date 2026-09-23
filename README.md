# clinrag

Does retrieval-grounding actually reduce hallucination in LLM explanations of genomic
variants — and what kind of hallucination is left over?

The contribution is the **faithfulness metric** (atomic-claim decomposition + verification
against an evidence corpus, validated against human annotation) and the **hallucination
taxonomy**. The variant scorer and the retriever are components.

See [docs/PLAN.md](docs/PLAN.md) for the 14-week plan and
[docs/eval_protocol.md](docs/eval_protocol.md) for the protocol that must be frozen before
any generation run.

## Layout

```
configs/                 experiment configuration (yaml)
data/{raw,interim,processed}   gitignored
docs/PLAN.md             phase plan
docs/eval_protocol.md    frozen decisions + metric definition  <- Phase 0 deliverable
scripts/                 numbered drivers, one per phase step
  01_build_variant_set.py      ClinVar -> labelled, gene-split, evidence-gated set
  02_probe_corpus_coverage.py  how much variant-level evidence exists per variant
  03_build_clinvar_text.py     submitter comments -> sanitized, deduped
  04_build_evidence_pools.py   both sources -> ranked pool + evidence budget report
  05_eval_retrieval.py         retrieval vs the expert-citation gold set
  06_generate.py               four-arm generation (--dry-run costs nothing)
src/clinrag/
  data/                  Phase 1  ClinVar -> labelled, gene-split variant set
  scorer/                Phase 2  genomic FM + baselines (CADD, REVEL, AlphaMissense)
  retrieval/             Phase 3  PubTator3 corpus, BM25 / dense / hybrid
  generation/            Phase 4  grounded / ungrounded / distractor arms
  metric/                Phase 5  claim decomposition + verification
  analysis/              Phase 6-7 experiment aggregation, taxonomy coding
tests/
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Phase 1 — build the variant set

```powershell
python scripts/01_build_variant_set.py --config configs/data.yaml
```

Downloads `variant_summary.txt.gz` (~100 MB), keeps GRCh38 SNVs at >=2 review stars, balances
pathogenic/benign, caps per-gene contribution, and splits **by gene**. Writes
`data/processed/variants.parquet` plus a set card with the exact counts for the paper.

VUS go to `variants_vus.parquet` and stay out of the main experiment — there is no ground
truth to be faithful to.

## Phase 3 — evidence

```powershell
python scripts/03_build_clinvar_text.py          # sanitized submitter comments
python scripts/04_build_evidence_pools.py --split dev
python scripts/05_eval_retrieval.py --split dev --cached-only
```

## Phase 4 — generation

Always dry-run first: it builds every prompt, writes the invariance audit, and spends nothing.

```powershell
python scripts/06_generate.py --split dev --dry-run
python scripts/06_generate.py --split dev --backend claude --batch   # costs money
```

Needs `pip install anthropic` and credentials (`ant auth login`, or `ANTHROPIC_API_KEY`).
Note that **temperature is unavailable** on current models - see `docs/eval_protocol.md` §13.

```powershell
python -m pytest tests -q
```
