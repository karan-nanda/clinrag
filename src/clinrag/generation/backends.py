"""Phase 4: generation backends.

Two things here are experiment design, not plumbing:

**Temperature does not exist on current Claude models.** `temperature`, `top_p` and `top_k`
were removed on Opus 5, Sonnet 5, Opus 4.7/4.8 and Fable, and sending them returns a 400.
The plan's "temperature 0 for the main run" is therefore not implementable on the API arm.
We do not silently drop the intent: `ClaudeGenerator` fixes `effort` instead, records the
exact model and settings on every generation, and the protocol calls for N replicates to
characterize run-to-run variance rather than asserting determinism we cannot obtain. An
open-weight generator run locally CAN set temperature 0, so the two generators differ in
determinism -- that asymmetry is documented, not hidden.

**The Batch API halves the cost.** The main run is 4 arms x 2 models x |variants|; at 1,000
variants that is 8,000 generations per model. Batch submission is the default path. Batch
results come back in arbitrary order, so everything is keyed by `custom_id`, never position.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

# Current model IDs. Do not append date suffixes -- these strings are complete.
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5"

MAX_TOKENS = 2000  # explanations are 4-8 sentences; this leaves generous headroom


def make_client():
    """Anthropic client, adding the workspace header when the key needs one.

    An org-level API key is not scoped to a workspace and the API rejects it with a 400
    unless `anthropic-workspace-id` is sent. Set ANTHROPIC_WORKSPACE_ID (a `wrkspc_...` id,
    not a secret) to supply it, or use a workspace-scoped key and leave it unset.
    """
    import os

    import anthropic

    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace:
        return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace})
    return anthropic.Anthropic()


@dataclass
class Generation:
    """One explanation plus everything needed to reproduce and audit it."""

    variation_id: str
    arm: str
    model: str
    text: str
    settings: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def custom_id(self) -> str:
        return make_custom_id(self.variation_id, self.arm, self.model)

    def as_dict(self) -> dict:
        return asdict(self)


def make_custom_id(variation_id: str, arm: str, model: str, replicate: int = 0) -> str:
    """Stable key for batch results, which return in arbitrary order.

    Batch `custom_id` is length-limited, so long model ids are hashed rather than embedded.
    """
    tag = hashlib.blake2b(model.encode(), digest_size=4).hexdigest()
    return f"{variation_id}__{arm}__{tag}__r{replicate}"


def parse_custom_id(custom_id: str) -> dict:
    vid, arm, tag, rep = custom_id.split("__")
    return {"variation_id": vid, "arm": arm, "model_tag": tag, "replicate": int(rep.lstrip("r"))}


class Generator(Protocol):
    name: str

    def generate(self, system: str, user: str) -> Generation: ...


class EchoGenerator:
    """Deterministic stub. Exercises the whole pipeline without spending money.

    It returns a fixed, plausible-shaped explanation so Phases 4-7 can be wired and tested
    end to end before any paid run. Its output is obviously synthetic on inspection, so it
    can never be mistaken for a real result in the output files.
    """

    name = "echo-stub"

    def generate(self, system: str, user: str) -> Generation:
        gene = "the gene"
        for line in user.splitlines():
            if line.startswith("Gene: "):
                gene = line[6:].strip()
                break
        text = (
            f"SYNTHETIC STUB OUTPUT. This variant lies in {gene}. "
            "It is reported in affected individuals in the cited literature. "
            "Functional assays indicate a measurable effect on protein activity. "
            "The allele frequency in population databases is consistent with this assessment."
        )
        return Generation(
            variation_id="", arm="", model=self.name, text=text,
            settings={"stub": True}, stop_reason="end_turn",
        )


class ClaudeGenerator:
    """Anthropic API backend.

    `temperature` is intentionally absent -- it is rejected by current models. Determinism is
    not claimed; run replicates and report variance.
    """

    def __init__(self, model: str = OPUS, effort: str = "medium", max_tokens: int = MAX_TOKENS):
        self.client = make_client()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.name = model

    @property
    def settings(self) -> dict:
        return {
            "model": self.model,
            "effort": self.effort,
            "max_tokens": self.max_tokens,
            "thinking": "adaptive (default)",
            "temperature": "unavailable on this model",
        }

    def _request_params(self, system: str, user: str) -> dict:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "output_config": {"effort": self.effort},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

    def generate(self, system: str, user: str) -> Generation:
        import anthropic

        try:
            resp = self.client.messages.create(**self._request_params(system, user))
        except anthropic.APIStatusError as exc:
            return Generation(
                variation_id="", arm="", model=self.model, text="",
                settings=self.settings, error=f"{exc.status_code}: {exc.message}",
            )
        except anthropic.APIConnectionError as exc:
            return Generation(
                variation_id="", arm="", model=self.model, text="",
                settings=self.settings, error=f"connection: {exc}",
            )

        return Generation(
            variation_id="", arm="", model=self.model,
            text=_text_of(resp),
            settings=self.settings,
            usage=_usage_of(resp),
            stop_reason=resp.stop_reason,
        )

    # ------------------------------------------------------------------ batch

    def submit_batch(self, items: list[tuple[str, str, str]]) -> str:
        """Submit (custom_id, system, user) triples. Returns the batch id.

        Batch runs at 50% cost and is the default for the main experiment.
        """
        from anthropic.types.messages.batch_create_params import Request
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

        requests = [
            Request(
                custom_id=cid,
                params=MessageCreateParamsNonStreaming(**self._request_params(system, user)),
            )
            for cid, system, user in items
        ]
        batch = self.client.messages.batches.create(requests=requests)
        return batch.id

    def wait(self, batch_id: str, poll_seconds: int = 60, timeout_seconds: int = 86_400) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.client.messages.batches.retrieve(batch_id).processing_status
            if status == "ended":
                return status
            time.sleep(poll_seconds)
        raise TimeoutError(f"batch {batch_id} still {status} after {timeout_seconds}s")

    def collect(self, batch_id: str) -> list[Generation]:
        """Read batch results. Keyed by custom_id -- results arrive in arbitrary order."""
        out: list[Generation] = []
        for entry in self.client.messages.batches.results(batch_id):
            meta = parse_custom_id(entry.custom_id)
            kind = entry.result.type
            if kind != "succeeded":
                out.append(
                    Generation(
                        variation_id=meta["variation_id"], arm=meta["arm"], model=self.model,
                        text="", settings=self.settings, error=kind,
                    )
                )
                continue
            msg = entry.result.message
            out.append(
                Generation(
                    variation_id=meta["variation_id"], arm=meta["arm"], model=self.model,
                    text=_text_of(msg), settings=self.settings,
                    usage=_usage_of(msg), stop_reason=msg.stop_reason,
                )
            )
        return out


def _text_of(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _usage_of(msg) -> dict:
    u = getattr(msg, "usage", None)
    if u is None:
        return {}
    return {
        k: getattr(u, k, None)
        for k in ("input_tokens", "output_tokens",
                  "cache_read_input_tokens", "cache_creation_input_tokens")
    }


def write_jsonl(generations: list[Generation], path) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for g in generations:
            fh.write(json.dumps(g.as_dict(), ensure_ascii=False) + "\n")
