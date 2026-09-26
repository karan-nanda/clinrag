# Pilot results — 2026-09-26

**These are pipeline-validation numbers, not findings.** n is 6–10 paired variants per
comparison and the metric is not yet validated against human annotation. Nothing here is
reportable. What they establish is that the pipeline produces a coherent, inspectable signal
end to end.

Setup: 10 dev variants x 4 arms, generator `claude-opus-5` (effort medium), 34 generations,
0 errors, **$1.32**. Judge `claude-opus-5` (effort medium) over 259 claims, 0 errors,
decomposer = rule-based sentence splitter.

## Rates by arm

| arm | expl | claims | supported | unsupported | contradicted | conflation |
|---|---|---|---|---|---|---|
| `grounded` | 10 | 75 | **0.221** | **0.764** | 0.014 | 0.027 |
| `ungrounded` | 10 | 77 | 0.077 | 0.898 | 0.025 | 0.014 |
| `distractor_same_gene` | 6 | 46 | 0.066 | 0.783 | **0.152** | 0.021 |
| `distractor_other_gene` | 8 | 61 | 0.054 | 0.877 | 0.069 | 0.000 |

Overall verdicts: 29 supported, 216 unsupported, 14 contradicted.

## Paired comparisons vs `ungrounded` (bootstrap over variants)

- `grounded` unsupported: **-0.134 [-0.221, -0.034]** — CI excludes zero
- `distractor_same_gene` contradicted: **+0.110 [+0.006, +0.235]** — CI excludes zero
- `distractor_other_gene` contradicted: **+0.038 [+0.004, +0.083]** — CI excludes zero
- every other comparison crosses zero

Directionally this is what the study predicts: grounding lowers the unsupported rate and
roughly triples the supported rate, while irrelevant evidence *induces contradictions* — and
same-gene distractors induce more than other-gene ones (0.152 vs 0.069), which is the
ordering the design expects if same-gene evidence is the more seductive error.

**Do not believe the intervals.** They come from 6–10 paired variants. A bootstrap over six
observations reproduces the sample, not the population.

## Judge behaviour — inspected by hand

Read every contradicted verdict and a sample of supported ones.

- **Rationales are specific and track the passages.** Example: a claim that population
  frequency constitutes "stand-alone strong benign evidence" was marked contradicted because
  the passage explicitly says the frequency data are insufficient to draw any conclusion.
  That is a correct and non-trivial judgement.
- **No obvious knowledge leakage** in the supported sample — verdicts cite passage ids and
  the rationales describe what those passages say, not what the judge knows about genetics.
  This was the failure mode most likely to inflate the grounded arm; it did not appear, but
  a sample read is not a measurement. Human validation still decides this.
- **Meta-commentary artifact, small.** Models sometimes write claims *about the evidence set*
  rather than the variant ("the retrieved literature is unrelated and carries no weight").
  The judge scores those as factual claims. **2 of 14** contradictions and **6 of 259** claims
  overall — present, worth a footnote, not driving the effect.

## Conflation barely fires

0.000–0.027 across arms, about 5 claims in total. Too few to say anything. Either the
pattern is genuinely rare, or the detector is too strict (it requires a `variant`-scoped
claim supported *only* by gene-level passages, and only 29 claims were supported at all).
With 216 of 259 claims unsupported, there is little supported material in which conflation
*could* be detected. Worth revisiting once the corpus or the arms produce more support.

## What this does not establish

- Nothing about statistical reliability — n is far too small.
- Nothing validated: no human annotation, no kappa, no per-label judge accuracy.
- Judge self-preference is **unmeasured**: `claude-opus-5` judged `claude-opus-5`, and every
  verdict is flagged `same_family_as_generator`. This is exactly the case the validation
  sample over-samples.
- The rule-based decomposer was used, not the LLM one. The headline should be reported under
  both.
