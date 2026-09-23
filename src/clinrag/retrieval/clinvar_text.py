"""Phase 3 source: ClinVar submitter free-text comments.

The free text is NOT in `variant_summary.txt`. It lives in the `Description` column of
`submission_summary.txt.gz` -- one row per submitted record (SCV), so a variant with five
submitting labs has five descriptions.

This is the same field Li et al. 2026 (ClinVar-BERT) mined, and it comes with their warning
attached: these summaries state the classification. Everything loaded here must pass through
`sanitize.py` before it reaches an evidence pool. See docs/related_work.md §1.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd
from tqdm import tqdm

KEEP = [
    "VariationID", "ClinicalSignificance", "DateLastEvaluated", "Description",
    "ReviewStatus", "CollectionMethod", "Submitter", "SCV", "SubmittedGeneSymbol",
]


def find_header(path: Path) -> tuple[int, list[str]]:
    """Return (rows_to_skip, column_names).

    The file opens with a prose preamble of `#`-prefixed lines, one of which is the
    *documentation* line `#VariationID:  the identifier assigned by ClinVar...`. Matching on
    `#VariationID` alone picks up that prose line and yields a single bogus column, so the
    tab is part of the match.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if line.startswith("#VariationID\t"):
                cols = line.lstrip("#").rstrip("\n").split("\t")
                return i + 1, cols
    raise ValueError(f"no '#VariationID' header line found in {path}")


def load_descriptions(
    path: Path,
    variation_ids: set[str] | None = None,
    min_chars: int = 100,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    """Load submitter descriptions, optionally restricted to a set of VariationIDs.

    `min_chars=100` matches the threshold Li et al. used to drop uninformative records.
    Descriptions of "-" (ClinVar's null) are dropped.
    """
    skiprows, cols = find_header(path)
    opener = gzip.open if str(path).endswith(".gz") else open

    kept: list[pd.DataFrame] = []
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        reader = pd.read_csv(
            fh,
            sep="\t",
            skiprows=skiprows,
            names=cols,
            chunksize=chunksize,
            dtype=str,
            low_memory=False,
            quoting=3,  # csv.QUOTE_NONE: descriptions contain unbalanced quote characters
        )
        for chunk in tqdm(reader, desc="loading submitter comments"):
            chunk["VariationID"] = chunk["VariationID"].astype(str).str.strip()
            if variation_ids is not None:
                chunk = chunk[chunk["VariationID"].isin(variation_ids)]
            if chunk.empty:
                continue
            desc = chunk["Description"].fillna("").str.strip()
            chunk = chunk[desc.ne("") & desc.ne("-") & (desc.str.len() >= min_chars)]
            if not chunk.empty:
                kept.append(chunk[[c for c in KEEP if c in chunk.columns]])

    if not kept:
        return pd.DataFrame(columns=KEEP)

    out = pd.concat(kept, ignore_index=True)
    out["DateLastEvaluated"] = pd.to_datetime(out["DateLastEvaluated"], errors="coerce")
    return out.reset_index(drop=True)
