"""In-memory data models for AI background image-generation jobs.

These models are pure Pydantic data definitions consumed by the JobManager
service and the ``image_jobs`` router. They are deliberately kept free of
service logic — the JobManager owns lifecycle transitions, persistence, and
concurrency control.

``GenerationJob`` is intentionally not persisted to disk. If the backend
process restarts, in-flight jobs are lost; status reads then return 404 and
clients must resubmit. Already-applied candidates remain on disk and on
``ProjectState``.
"""

from typing import Literal

from pydantic import BaseModel, model_validator

JobStatus = Literal["pending", "running", "succeeded", "failed"]
GenerationTargetKind = Literal["whole_video", "section"]


class GenerationTarget(BaseModel):
    """Where a generated image is bound.

    - ``whole_video``: applies to the project's single ``background_image``.
      Both ``start_index`` and ``end_index`` MUST be ``None``.
    - ``section``: applies to a contiguous range of subtitle segments.
      Both ``start_index`` and ``end_index`` MUST be non-``None`` integers.

    The ``model_validator`` below enforces this invariant; the router relies
    on the resulting ``ValidationError`` to convert bad index combinations
    into HTTP 422 responses (Requirement 3).
    """

    kind: GenerationTargetKind
    start_index: int | None = None
    end_index: int | None = None

    @model_validator(mode="after")
    def _validate_indices_match_kind(self) -> "GenerationTarget":
        if self.kind == "whole_video":
            if self.start_index is not None or self.end_index is not None:
                raise ValueError(
                    "start_index and end_index must be omitted when "
                    "kind is 'whole_video'"
                )
        else:  # kind == "section"
            if self.start_index is None or self.end_index is None:
                raise ValueError(
                    "start_index and end_index are required when "
                    "kind is 'section'"
                )
        return self


class CandidateImage(BaseModel):
    id: str
    url: str
    filename: str


class GenerationJob(BaseModel):
    id: str
    project_id: str
    owner_id: str
    prompt: str
    image_count: int
    target: GenerationTarget
    reference_image_filename: str | None = None
    status: JobStatus
    candidates: list[CandidateImage] = []
    error_message: str | None = None
    created_at: str
    updated_at: str
