"""The run record: the single seam between the network and everything else.

A run record pairs *what was played* (the fixture manifest) with *what the server
said* (raw events, echoed config, returned word timings). Everything downstream --
the comparison, the viewer, the published report -- reads run records and never
re-runs anything.

Two rules shape the models here:

* **No model has a field that could hold a credential.** Run records are designed
  to be published. Every model forbids extra fields, so a key cannot be smuggled
  in as an unrecognised attribute.
* **The manifest's marker positions come from the composition**, not from a
  separately maintained list, so a declared position and an actual position
  cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from scribe_timeline.audio.timeline import ComposedTimeline, Placement

#: A marker position: a sample index, so never negative.
SampleIndex = Annotated[int, Field(ge=0)]

CommitStrategy = Literal["vad", "manual"]


class _Strict(BaseModel):
    """Base model that refuses unknown fields.

    This is what makes "no credential is admissible" a structural guarantee
    rather than a convention someone has to remember.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Segment(_Strict):
    """A clip placed in the composed timeline."""

    clip_id: str
    start_sample: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    marker_text: str | None = None


class Manifest(_Strict):
    """What was played: sample rate, layout, and realised marker positions."""

    condition_id: str
    sample_rate: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    markers: dict[str, SampleIndex] = Field(min_length=1)
    """At least one marker is required.

    A run record with no marker position cannot support a measurement, so
    producing one is a bug rather than an empty-but-valid record.
    """
    segments: tuple[Segment, ...] = ()

    @field_validator("markers")
    @classmethod
    def _markers_within_audio(cls, markers: dict[str, int], info: ValidationInfo) -> dict[str, int]:
        """Every marker must sit inside the audio, and not at its very end.

        A marker at or past `sample_count` would claim a word occurs at a position
        holding no audio -- a wrong number that still validates.
        """
        sample_count = info.data.get("sample_count")
        if sample_count is None:
            return markers
        for text, position in markers.items():
            if position >= sample_count:
                raise ValueError(
                    f"marker {text!r} at sample {position} is outside audio of "
                    f"{sample_count} samples"
                )
        return markers

    @classmethod
    def from_composition(
        cls,
        *,
        condition_id: str,
        composed: ComposedTimeline,
        placements: tuple[Placement, ...],
        sample_rate: int,
        clip_lengths: Mapping[str, int],
    ) -> Manifest:
        """Build a manifest from audio that was actually composed.

        Marker positions come from the composition rather than from the placement
        request, so they describe where audio really landed.

        `clip_lengths` is required and must cover every placement. Defaulting a
        missing length to zero would describe a segment that is not there, which
        renders as a plausible but wrong timeline.
        """
        unknown = sorted({p.clip_id for p in placements} - set(clip_lengths))
        if unknown:
            raise ValueError(f"no clip length given for placed clip(s): {', '.join(unknown)}")

        return cls(
            condition_id=condition_id,
            sample_rate=sample_rate,
            sample_count=composed.sample_count,
            markers=dict(composed.markers),
            segments=tuple(
                Segment(
                    clip_id=p.clip_id,
                    start_sample=p.start_sample,
                    sample_count=clip_lengths[p.clip_id],
                    marker_text=p.marker_text,
                )
                for p in placements
            ),
        )


#: Substrings that mark a key as credential-shaped. Matched case-insensitively
#: against every key at every depth of a free-form payload.
CREDENTIAL_KEY_MARKERS = (
    "key",
    "token",
    "secret",
    "auth",
    "credential",
    "password",
    "passwd",
    "session_id",
    "bearer",
)


def _credential_keys(node: object, path: str = "payload") -> list[str]:
    """Every credential-shaped key reachable inside a free-form structure.

    `extra="forbid"` only constrains *named* fields. A free-form dict inside a
    named field is the route a secret actually takes into a run record, so the
    payload is walked rather than trusted.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in CREDENTIAL_KEY_MARKERS):
                found.append(f"{path}.{key}")
            found.extend(_credential_keys(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(_credential_keys(value, f"{path}[{index}]"))
    return found


class RawEvent(_Strict):
    """One event exactly as received, kept unedited.

    The payload is walked for credential-shaped keys and the event is rejected if
    any are found. Failing loudly is deliberate: a redacted payload would be
    silently incomplete evidence, and a published run record must be trustworthy
    enough to reason from.
    """

    type: str
    received_at_ms: float = Field(ge=0)
    clock_origin: Literal["monotonic_since_connect"] = "monotonic_since_connect"
    """What `received_at_ms` is measured from.

    Recorded explicitly so arrival latency can be told apart from timestamps on
    the audio sample clock. Without an origin, the number is ambiguous in a
    published record.
    """
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _reject_credentials(cls, payload: dict[str, Any]) -> dict[str, Any]:
        leaked = _credential_keys(payload)
        if leaked:
            raise ValueError(
                "event payload contains credential-shaped keys: "
                f"{', '.join(sorted(leaked))}; refusing to build a publishable run record"
            )
        return payload


class EchoedSessionConfig(_Strict):
    """The session configuration the server echoed back.

    Recorded rather than the values that were sent: the original report found the
    server echoing VAD durations that did not account for the observed offset, so
    sent and echoed values are not assumed to agree.
    """

    model_id: str
    language_code: str | None = None
    sample_rate: int = Field(gt=0)
    include_timestamps: bool
    commit_strategy: CommitStrategy
    vad_silence_threshold_secs: float | None = None
    vad_threshold: float | None = None
    min_speech_duration_ms: int | None = None
    min_silence_duration_ms: int | None = None


class WordTiming(_Strict):
    """One returned word and the interval the server assigned it."""

    text: str
    start_ms: float = Field(ge=0)
    end_ms: float = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class RunRecord(_Strict):
    """One condition, one repeat, one strategy: everything needed to re-derive
    the result without re-running it."""

    schema_version: int
    run_id: str
    condition_id: str
    commit_strategy: CommitStrategy
    repeat_index: int = Field(ge=0)
    manifest: Manifest
    events: tuple[RawEvent, ...] = ()
    echoed_config: EchoedSessionConfig
    words: tuple[WordTiming, ...] = ()
    api_key_present: bool = False
    """Whether a credential was in the environment for this run.

    Recorded for honesty about the run environment. The credential itself is
    never stored, so run records remain safe to publish.
    """
