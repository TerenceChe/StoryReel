"""Typed exceptions for the image-job JobManager and router layer.

These are pure exception definitions — no service logic. The ``image_jobs``
router maps each subclass to a specific HTTP status code (see
``_map_exception_to_http`` in ``backend/routers/image_jobs.py``):

- ``ImageJobNotFoundError``        -> 404
- ``ImageJobConcurrencyError``     -> 429
- ``ImageJobInvalidStateError``    -> 409
- ``ImageJobValidationError``      -> 422
- ``ImageJobCandidateNotFoundError`` -> 422
- ``ProviderAuthenticationError``  -> 503 (and flips capability state)

A common ``ImageJobError`` base exists so callers (and tests) can use a
single ``except`` clause as a catch-all for image-job errors.
"""


class ImageJobError(Exception):
    """Base class for image-job errors raised by the JobManager / router layer."""


class ImageJobNotFoundError(ImageJobError):
    """The requested job_id is unknown to the JobManager."""


class ImageJobConcurrencyError(ImageJobError):
    """The Owner is already at the per-user concurrent-job cap."""


class ImageJobInvalidStateError(ImageJobError):
    """The job is not in a state that permits the requested operation
    (for example, attaching a reference image after the job has started).
    """


class ImageJobValidationError(ImageJobError):
    """The submitted job parameters violate a business rule
    (image_count out of range, section indices out of range, etc.).
    """


class ImageJobCandidateNotFoundError(ImageJobError):
    """The supplied candidate_id is not present in the job's candidates."""


class ProviderAuthenticationError(ImageJobError):
    """The configured Image_Generation_Provider rejected the request with
    an authentication / authorization error (HTTP 401 or 403).

    The JobManager catches this specifically: it marks the job ``failed``
    with the generic user-facing message AND flips the process-level
    capability state to disabled for the remainder of the session.
    """
