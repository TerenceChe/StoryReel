"""Static import-check test enforcing router isolation from provider SDKs.

The image-jobs router (`backend/routers/image_jobs.py`) MUST depend only
on the abstract :class:`ImageGenerationBackend` interface. Provider
selection happens once at startup in `backend/dependencies.py`; the
router is provider-agnostic by design.

This test parses the router source with the `ast` module and walks every
`import` and `from ... import ...` statement, asserting that no
imported module names a known concrete provider SDK.

Validates: Requirement 8.3
"""

from __future__ import annotations

import ast
from pathlib import Path

# Root package names of provider SDKs that the router MUST NOT import.
# A match against the *first* dotted segment is sufficient: a hypothetical
# `import openai.images` or `from stability_sdk.client import X` is just
# as forbidden as a bare `import openai`.
FORBIDDEN_PROVIDER_ROOTS = frozenset({"openai", "stability_sdk"})

ROUTER_PATH = (
    Path(__file__).resolve().parent.parent / "routers" / "image_jobs.py"
)


def _root_segment(dotted_name: str) -> str:
    """Return the first segment of a dotted module name."""
    return dotted_name.split(".", 1)[0]


def test_image_jobs_router_does_not_import_provider_sdks() -> None:
    """The router source contains no import of a known provider SDK.

    Validates: Requirement 8.3
    """
    source = ROUTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROUTER_PATH))

    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.name or ""
                if _root_segment(imported) in FORBIDDEN_PROVIDER_ROOTS:
                    offenders.append(
                        f"line {node.lineno}: import {imported}"
                    )
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has node.module == None — those are
            # relative imports and cannot reach an external SDK.
            module = node.module or ""
            if not module:
                continue
            if _root_segment(module) in FORBIDDEN_PROVIDER_ROOTS:
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(
                    f"line {node.lineno}: from {module} import {names}"
                )

    assert not offenders, (
        "backend/routers/image_jobs.py must not import any concrete "
        "provider SDK (Requirement 8.3). Offending imports:\n  - "
        + "\n  - ".join(offenders)
    )
