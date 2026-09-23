"""Phase 1: build the labelled variant set from ClinVar `variant_summary.txt`.

Design notes that are load-bearing for the experiment:

* Review status >= 2 stars only. Below that, labels are single-submitter or conflicting and
  the "ground truth" is not reliable enough to call a model wrong.
* VUS is excluded from the main set (there is no ground truth to be faithful to). It is kept
  in a separate file for the secondary analysis.
* Nothing here splits the data. Splitting is gene-level and lives in `splits.py`.
"""

from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

# ClinVar review-status strings -> star rating.
# https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/
REVIEW_STARS: dict[str, int] = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, conflicting classifications": 1,
    "criteria provided, conflicting interpretations": 1,  # pre-2024 wording
    "criteria provided, single submitter": 1,
    "no assertion criteria provided": 0,
    "no assertion provided": 0,
    "no classification provided": 0,
    "no classifications from unflagged records": 0,
    "no classification for the single variant": 0,
}

# Columns we need. ClinVar adds columns over time, so select by name, never by position.
USECOLS = [
    "#AlleleID", "Type", "Name", "GeneSymbol", "GeneID", "ClinicalSignificance",
    "LastEvaluated", "RS# (dbSNP)", "PhenotypeList", "Origin", "Assembly",
    "Chromosome", "Start", "Stop", "ReferenceAlleleVCF", "AlternateAlleleVCF",
    "ReviewStatus", "NumberSubmitters", "VariationID", "PositionVCF",
]

RENAME = {
    "#AlleleID": "allele_id",
    "GeneSymbol": "gene",
    "GeneID": "gene_id",
    "RS# (dbSNP)": "rsid",
    "VariationID": "variation_id",
    "PositionVCF": "pos",
    "ReferenceAlleleVCF": "ref",
    "AlternateAlleleVCF": "alt",
    "Chromosome": "chrom",
    "PhenotypeList": "phenotypes",
    "LastEvaluated": "last_evaluated",
    "NumberSubmitters": "n_submitters",
}


@dataclass(frozen=True)
class FilterSpec:
    min_review_stars: int = 2
    variant_types: tuple[str, ...] = ("single nucleotide variant",)
    include_vus: bool = False
    drop_somatic: bool = True
    assembly: str = "GRCh38"


def download(url: str, dest: Path, force: bool = False) -> Path:
    """Stream the ClinVar dump to `dest`. Skips if the file is already present."""
    dest = Path(dest)
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(tmp, "wb") as fh, tqdm(
            total=total or None, unit="B", unit_scale=True, desc=dest.name
        ) as bar:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                bar.update(len(chunk))
    shutil.move(str(tmp), str(dest))
    return dest


def review_stars(status: pd.Series) -> pd.Series:
    key = status.fillna("").str.strip().str.lower()
    return key.map(REVIEW_STARS).fillna(-1).astype(int)


def normalise_significance(sig: pd.Series) -> pd.Series:
    """Collapse ClinVar free-text classifications into {pathogenic, benign, vus, other}.

    Order matters. "Pathogenic/Likely pathogenic" must not fall through to `other`, while
    "Conflicting classifications of pathogenicity" must NOT become pathogenic.
    """
    s = sig.fillna("").str.strip().str.lower()
    out = pd.Series("other", index=s.index, dtype="object")

    conflicting = s.str.contains("conflicting")
    vus = s.str.contains("uncertain significance") | s.str.contains("uncertain risk")
    path = s.str.contains("pathogenic") & ~conflicting & ~vus
    ben = s.str.contains("benign") & ~conflicting & ~vus

    # A record reading as both (should not happen at >=2 stars) is left as `other`.
    both = path & ben
    out[path & ~both] = "pathogenic"
    out[ben & ~both] = "benign"
    out[vus] = "vus"
    return out


def load_and_filter(path: Path, spec: FilterSpec, chunksize: int = 250_000) -> pd.DataFrame:
    """Read the gzipped dump in chunks, keeping only rows that pass `spec`.

    VUS rows survive here regardless of `spec.include_vus`; the caller splits them off so the
    secondary analysis has them. `other` (drug response, risk factor, ...) is dropped.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    kept: list[pd.DataFrame] = []

    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        reader = pd.read_csv(fh, sep="\t", chunksize=chunksize, low_memory=False, dtype=str)
        for chunk in tqdm(reader, desc="filtering ClinVar"):
            missing = [c for c in USECOLS if c not in chunk.columns]
            if missing:
                raise KeyError(
                    "ClinVar schema changed; missing columns: "
                    f"{missing}. Update USECOLS in clinrag/data/clinvar.py."
                )
            df = chunk[USECOLS].copy()

            df = df[df["Assembly"] == spec.assembly]
            df = df[df["Type"].str.lower().isin(spec.variant_types)]

            df["review_stars"] = review_stars(df["ReviewStatus"])
            df = df[df["review_stars"] >= spec.min_review_stars]

            df["label"] = normalise_significance(df["ClinicalSignificance"])
            df = df[df["label"].isin({"pathogenic", "benign", "vus"})]

            if spec.drop_somatic:
                df = df[~df["Origin"].fillna("").str.contains("somatic", case=False)]

            # Phase 2 needs clean VCF-style alleles to build ref/alt sequence windows.
            for col in ("ReferenceAlleleVCF", "AlternateAlleleVCF"):
                df = df[df[col].fillna("").str.fullmatch(r"[ACGT]+")]

            if len(df):
                kept.append(df)

    if not kept:
        return pd.DataFrame(columns=list(RENAME.values()) + ["review_stars", "label"])

    out = pd.concat(kept, ignore_index=True).rename(columns=RENAME)
    # ClinVar stores the bare number and uses -1 for "none". Phase 3 queries PubTator3 with
    # the canonical rsID, so normalise here rather than at every call site.
    rs = out["rsid"].fillna("").str.strip()
    out["rsid"] = ("rs" + rs).where(rs.str.fullmatch(r"\d+") & rs.ne("-1"), pd.NA)
    out["last_evaluated"] = pd.to_datetime(out["last_evaluated"], errors="coerce")
    out["pos"] = pd.to_numeric(out["pos"], errors="coerce").astype("Int64")

    # One row per variant: ClinVar lists a variant once per RCV accession.
    out = out.drop_duplicates(subset=["variation_id"])

    # Multi-gene rows ("A;B") go: a gene-level split needs exactly one gene per variant.
    gene = out["gene"].fillna("")
    out = out[~gene.str.contains(";") & gene.ne("") & gene.ne("-")]

    return out.reset_index(drop=True)


def balance_paired(df: pd.DataFrame, target_n: int, seed: int = 0) -> pd.DataFrame:
    """Sample variants as same-gene, same-label **pairs**.

    The `distractor_same_gene` arm needs, for each variant, another variant in the same gene
    to borrow evidence from. Under plain `balance()` only 491 of 1,000 variants had a
    same-gene same-label partner, so that arm could run on half the set.

    Taking exactly two variants per (gene, label) fixes it and keeps the per-gene cap that
    stops BRCA1 dominating -- two is the cap. Gene-level splitting keeps a pair in one fold
    automatically, so a variant never sees its partner across a split boundary.

    Matching the distractor's label to the target's is deliberate: an opposite-label
    distractor would differ in both relevance *and* direction of evidence, confounding
    "ignores irrelevant evidence" with "is swayed by contrary evidence". Matched labels
    isolate variant identity as the only thing wrong with the evidence.
    """
    rng = df[df["label"].isin(("pathogenic", "benign"))].sample(frac=1.0, random_state=seed)
    per_class = target_n // 2

    parts = []
    for label in ("pathogenic", "benign"):
        sub = rng[rng["label"] == label]
        sizes = sub.groupby("gene").size()
        eligible = sizes[sizes >= 2].index
        pairs = sub[sub["gene"].isin(eligible)].groupby("gene", group_keys=False).head(2)

        n_genes = per_class // 2
        genes = list(dict.fromkeys(pairs["gene"]))[:n_genes]
        if len(genes) < n_genes:
            raise ValueError(
                f"only {len(genes)} genes have >=2 {label} variants; need {n_genes}. "
                "Lower sampling.target_n or set sampling.pair_by_gene: false."
            )
        parts.append(pairs[pairs["gene"].isin(genes)])

    return pd.concat(parts).sample(frac=1.0, random_state=seed).reset_index(drop=True)


def balance(
    df: pd.DataFrame,
    target_n: int,
    max_per_gene: int | None = 5,
    seed: int = 0,
) -> pd.DataFrame:
    """Down-sample to `target_n` rows with equal pathogenic/benign counts.

    `max_per_gene` caps how much any one gene (BRCA1, CFTR, ...) contributes, so the set is
    not effectively an evaluation on three genes.
    """
    pool = df[df["label"].isin(("pathogenic", "benign"))]
    pool = pool.sample(frac=1.0, random_state=seed)  # shuffle once, then take deterministically
    if max_per_gene:
        pool = pool.groupby(["gene", "label"], group_keys=False).head(max_per_gene)

    per_class = target_n // 2
    parts = []
    for label in ("pathogenic", "benign"):
        sub = pool[pool["label"] == label]
        if len(sub) < per_class:
            raise ValueError(
                f"only {len(sub)} {label} variants survive filtering; asked for {per_class}"
            )
        parts.append(sub.head(per_class))
    return pd.concat(parts).sample(frac=1.0, random_state=seed).reset_index(drop=True)
