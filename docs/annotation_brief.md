# Second annotator — what the task is and what it costs you

*A brief to send to a prospective annotator. Fill in the bracketed parts before sending.*

---

## The short version

I need a second person to label **50 statements** as supported / unsupported / contradicted
against a fixed set of evidence passages. It takes about **1.5–2 hours**. You need to be
comfortable reading clinical variant evidence; you do **not** need to know anything about the
project, and it is better if you don't.

If the calibration round goes well, the real round is **200 claims, roughly 4–6 hours**,
[negotiable / paid at X / acknowledged as Y — decide and state it].

## Why a second annotator is required

The project measures how often an LLM's explanation of a genetic variant makes claims the
evidence doesn't support. An automatic judge does the labelling at scale, but an automatic
judge is a measuring instrument, and an uncalibrated instrument produces numbers rather than
findings. Your labels are the calibration: agreement between two independent humans tells us
whether the task is even well-defined, and agreement between the judge and the humans tells
us whether the judge can be trusted.

Without this, there is no paper — the metric is the contribution.

## What you actually do

You get a spreadsheet. Each row has a CLAIM and a block of EVIDENCE passages. You put one
word in the `label` column:

| label | meaning |
|---|---|
| `supported` | The passages state or directly entail the claim. |
| `unsupported` | The passages neither state nor contradict it. |
| `contradicted` | A passage asserts something incompatible with the claim. |

**The one rule that matters: judge only what the passages say.**

A claim you know to be true, but which the passages do not state, is `unsupported` — not
`supported`. `unsupported` does not mean false. It records that this evidence does not settle
the question. In pilot annotation this is where nearly all disagreement comes from:
annotators import their own genetics knowledge. Resist that; it is the single most important
thing about the task.

If a claim is too vague to check, label it `unsupported` and write "vague" in the notes.

## Independence

Do not discuss any row with the other annotator until you have both finished. Agreement is
the measurement; conferring destroys it. The automatic judge's answers are deliberately
withheld from your sheet for the same reason.

## Who is suitable

Anyone who can read clinical variant evidence critically — a genetic counsellor, a clinical
lab scientist, a genetics PhD student, a bioinformatician who has worked with ClinVar. You do
**not** need to be a variant classification expert; the task is reading comprehension against
a fixed text, not clinical judgement.

Being unfamiliar with the project is an advantage, not a problem.

## Time, honestly

The 50 claims are grouped by variant, so you read **8 evidence blocks** (~6,000 characters
each), not 50. Budget ~40 minutes reading the evidence and ~45 minutes labelling. Most people
land around 1.5 hours; the first ten rows are the slowest.

## What you get

[State this explicitly before sending. Options: co-authorship, acknowledgement in the paper,
payment at a stated rate, or a reciprocal favour. Do not leave it vague — it is the main
reason people decline.]

## Practical

1. I send you `annotator_b.csv` and the annotation guide.
2. Open it in Excel, Sheets, or any CSV editor.
3. Fill only the `label` column. Leave everything else untouched, including row order.
4. Send it back. I run the agreement analysis; you get the results.

Questions about the *task* are welcome any time. Questions about what the "right" answer is
for a specific row, I can't answer until we're both done — that is the point.
