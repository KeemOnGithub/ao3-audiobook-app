"""Priority queue data models for TTS scheduling per Spec 04 section 5.1."""

from dataclasses import dataclass, field
import itertools

_enqueue_counter = itertools.count()


@dataclass(order=True)
class TTSTask:
    priority_tier: int
    # 0 = Urgent Hot-Swap
    # 1 = Fast-Start Chunk 0
    # 2 = Standard Chronological

    distance_key: int
    # Ordering key within the tier.

    enqueue_seq: int = field(
        default_factory=lambda: next(_enqueue_counter)
    )

    # Non-compared payload fields.
    chapter_uuid: str = field(compare=False, default="")
    run_generation: int = field(compare=False, default=0)
    segment_uuid: str = field(compare=False, default="")
    segment_order: int = field(compare=False, default=0)
    character_uuid: str = field(compare=False, default="")
    target_voice_version: int = field(compare=False, default=1)
    approved_review_revision: int = field(compare=False, default=-1)

