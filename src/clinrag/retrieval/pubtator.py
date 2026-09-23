"""Phase 3: evidence corpus from PubTator3.

PubTator3 is used instead of raw PubMed because its abstracts already carry normalized entity
annotations linking to genes, variants and rsIDs, which removes weeks of entity resolution.

The annotations buy us one thing beyond retrieval convenience, and it matters for Phase 7:
every passage can be tagged **variant-level** (the specific variant is annotated in it) or
**gene-level** (only the gene is). That tag is what lets us ask whether a model justified a
variant-specific claim using gene-level evidence -- the conflation category in the taxonomy.

API: https://www.ncbi.nlm.nih.gov/research/pubtator3-api/
NCBI asks for <=3 requests/second without an API key.

Response shape (verified against the live API, not the docs): the export endpoint returns
`{"PubTator3": [doc, ...]}`. Gene annotations carry a normalized symbol in `infons.name`
(the surface text may be "BRCA1/2"), and variant annotations carry `infons.rsids`,
`infons.hgvs` and `infons.clingen_id`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

BASE = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
SEARCH = f"{BASE}/search/"
EXPORT = f"{BASE}/publications/export/biocjson"

_MIN_INTERVAL = 0.34  # seconds between requests, ~3/s
_last_call = 0.0

VARIANT_TYPES = {"variant", "mutation", "dnamutation", "proteinmutation", "snp"}

# The export endpoint does NOT honour full=false for PMC open-access articles: it returns the
# whole body, reference list included. Measured on one BRCA1 pool, 926 of 2270 passages were
# `ref` entries. A bibliography string must never reach the evidence pool -- it would let a
# fabricated citation score as `supported`, which is exactly the Phase 7 category we are
# trying to detect.
ABSTRACT_SECTIONS = frozenset({"title", "abstract"})
BODY_SECTIONS = frozenset({"paragraph"})
NEVER_INDEX = frozenset(
    {"ref", "fig_caption", "table", "table_caption", "footnote", "front", "back"}
)
# `abstract_title_1`, `title_1`, `title_2` ... are section headings ("Methods", "Results").
_HEADING = re.compile(r"^(abstract_)?title_\d+$")


@dataclass
class Passage:
    """One retrievable unit of evidence (a title or an abstract)."""

    pmid: str
    section: str
    text: str
    order: int = 0          # position within the document; `section` alone is not unique
    year: int | None = None
    journal: str | None = None
    genes: list[str] = field(default_factory=list)       # normalized symbols
    gene_ids: list[str] = field(default_factory=list)    # NCBI Gene IDs
    rsids: list[str] = field(default_factory=list)
    hgvs: list[str] = field(default_factory=list)
    diseases: list[str] = field(default_factory=list)

    @property
    def passage_id(self) -> str:
        # A document has many `paragraph` passages and repeats `abstract_title_1`; without
        # `order`, 124 of 189 ids collided on a single measured pool.
        return f"{self.pmid}:{self.section}:{self.order}"

    def evidence_level(
        self,
        gene: str,
        rsid: str | None = None,
        gene_id: str | None = None,
        hgvs: str | None = None,
    ) -> str:
        """`variant` if this passage names the variant itself, else `gene`, else `other`.

        Phase 7 leans on this: a variant-specific claim supported only by `gene`-level
        passages is the gene/variant conflation pattern. Matching prefers NCBI Gene IDs over
        symbols, because symbols are ambiguous and aliased.
        """
        if rsid and _norm_rs(rsid) in {_norm_rs(r) for r in self.rsids}:
            return "variant"
        if hgvs and hgvs in self.hgvs:
            return "variant"
        if gene_id and str(gene_id) in self.gene_ids:
            return "gene"
        if gene and gene.upper() in {g.upper() for g in self.genes}:
            return "gene"
        return "other"


def _norm_rs(x: str) -> str:
    return str(x).strip().lower().lstrip("r").lstrip("s")


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _get(url: str, params: dict, retries: int = 3) -> requests.Response:
    last: requests.Response | None = None
    for attempt in range(retries):
        _throttle()
        r = requests.get(url, params=params, timeout=60)
        last = r
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2**attempt)
            continue
        r.raise_for_status()
        return r
    assert last is not None
    last.raise_for_status()
    return last


def search(query: str, max_pages: int = 1) -> list[str]:
    """Return PMIDs for a PubTator3 query.

    Query syntax accepts entity handles (`@GENE_BRCA1`), rsIDs (`rs80357906`) and free text.
    """
    pmids: list[str] = []
    for page in range(1, max_pages + 1):
        data = _get(SEARCH, {"text": query, "page": page}).json()
        hits = data.get("results", [])
        if not hits:
            break
        pmids.extend(str(h["pmid"]) for h in hits if h.get("pmid"))
        if page >= data.get("total_pages", 1):
            break
    return pmids


def fetch(pmids: list[str], batch_size: int = 100) -> list[Passage]:
    """Fetch annotated title/abstract passages in BioC JSON for the given PMIDs."""
    out: list[Passage] = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        r = _get(EXPORT, {"pmids": ",".join(batch), "full": "false"})
        for doc in _iter_docs(r.text):
            out.extend(_parse_doc(doc))
    return out


def _iter_docs(payload: str):
    """Unwrap the export payload.

    The live endpoint returns `{"PubTator3": [...]}`; older/BioC variants use `documents`,
    a bare list, or newline-delimited JSON. Handle all four rather than trusting one.
    """
    payload = payload.strip()
    if not payload:
        return
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        for line in payload.splitlines():
            if line.strip():
                yield from _iter_docs(line)
        return
    if isinstance(obj, list):
        yield from obj
    elif isinstance(obj, dict):
        for key in ("PubTator3", "documents"):
            if key in obj:
                yield from obj[key]
                return
        yield obj


def _parse_doc(doc: dict) -> list[Passage]:
    pmid = str(doc.get("pmid") or doc.get("id") or "")
    journal = doc.get("journal")
    doc_year = _year(str(doc.get("date") or ""))

    passages: list[Passage] = []
    for order, p in enumerate(doc.get("passages", [])):
        text = (p.get("text") or "").strip()
        if not text:
            continue
        infons = p.get("infons", {}) or {}
        genes, gene_ids, rsids, hgvs, diseases = [], [], [], [], []

        for ann in p.get("annotations", []):
            ai = ann.get("infons", {}) or {}
            kind = (ai.get("type") or "").lower()
            surface = (ann.get("text") or "").strip()

            if kind == "gene":
                # infons.name is the normalized symbol; surface text may be "BRCA1/2".
                genes.append(ai.get("name") or surface)
                gene_ids.extend(
                    g for g in str(ai.get("identifier") or "").split(";") if g.isdigit()
                )
            elif kind in VARIANT_TYPES:
                rs = ai.get("rsids") or ([ai["rsid"]] if ai.get("rsid") else [])
                rsids.extend(str(r) for r in rs)
                if ai.get("hgvs"):
                    hgvs.append(str(ai["hgvs"]))
            elif kind == "disease":
                diseases.append(ai.get("name") or surface)

        passages.append(
            Passage(
                pmid=pmid,
                section=str(infons.get("type", "abstract")).lower(),
                text=text,
                order=order,
                year=_year(str(infons.get("year") or "")) or doc_year,
                journal=journal,
                genes=sorted({g for g in genes if g}),
                gene_ids=sorted(set(gene_ids)),
                rsids=sorted({r for r in rsids if r}),
                hgvs=sorted(set(hgvs)),
                diseases=sorted({d for d in diseases if d}),
            )
        )
    return passages


def prepare_pool(
    passages: list[Passage],
    gene: str,
    rsid: str | None = None,
    gene_id: str | None = None,
    hgvs: str | None = None,
    sections: frozenset[str] = ABSTRACT_SECTIONS,
    min_chars: int = 40,
    drop_unrelated: bool = True,
) -> list[Passage]:
    """Turn a raw fetch into an indexable evidence pool.

    Raw fetches are cached unfiltered, so the pool can be rebuilt with different settings
    without re-hitting the rate-limited API. Four things are removed:

    1. Sections that are never evidence -- `ref` above all. Reference-list entries were 41%
       of one measured pool and would let a fabricated citation score as `supported`.
    2. Section headings ("Background", "Methods") -- they match any query and carry no claim.
    3. Passages shorter than `min_chars`.
    4. With `drop_unrelated`, passages where neither the gene nor the variant is annotated.

    Default `sections` is title+abstract, matching the plan. Pass `ABSTRACT_SECTIONS |
    BODY_SECTIONS` to include full-text paragraphs, and say so in the protocol if you do --
    it changes what "the corpus" means.
    """
    out: list[Passage] = []
    seen: set[str] = set()
    for p in passages:
        if p.section in NEVER_INDEX or _HEADING.match(p.section):
            continue
        if sections and p.section not in sections:
            continue
        if len(p.text) < min_chars:
            continue
        if drop_unrelated and p.evidence_level(gene, rsid, gene_id, hgvs) == "other":
            continue
        if p.passage_id in seen:
            continue
        seen.add(p.passage_id)
        out.append(p)
    return out


def pool_stats(passages: list[Passage], gene: str, rsid: str | None = None) -> dict[str, int]:
    """Counts for the retrieval report: how much of the pool is variant- vs gene-level."""
    levels = [p.evidence_level(gene, rsid) for p in passages]
    return {
        "n": len(passages),
        "variant_level": levels.count("variant"),
        "gene_level": levels.count("gene"),
        "other": levels.count("other"),
        "pmids": len({p.pmid for p in passages}),
    }


def _year(s: str) -> int | None:
    s = s.strip()[:4]
    return int(s) if s.isdigit() else None


def corpus_for_variant(
    gene: str,
    rsid: str | None,
    cache_dir: Path | None = None,
    max_pages: int = 1,
) -> list[Passage]:
    """Build the candidate pool for one variant: rsID hits first, then gene-level hits.

    This is the *candidate pool*, not the retrieval result -- ranking happens in `index.py`.
    Cached on disk because PubTator3 is rate-limited and the pool is reused across all four
    arms and both generator models.
    """
    key = f"{gene}__{rsid or 'nors'}".replace("/", "_")
    cache = (cache_dir / f"{key}.json") if cache_dir else None
    if cache and cache.exists():
        return [Passage(**p) for p in json.loads(cache.read_text(encoding="utf-8"))]

    pmids: list[str] = []
    if rsid:
        pmids.extend(search(rsid, max_pages=max_pages))
    pmids.extend(search(f"@GENE_{gene}", max_pages=max_pages))

    seen, unique = set(), []
    for p in pmids:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    passages = fetch(unique)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps([asdict(p) for p in passages], ensure_ascii=False), encoding="utf-8"
        )
    return passages
