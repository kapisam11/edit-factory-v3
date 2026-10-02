"""Expected V3 production failures with explicit operational categories."""
from __future__ import annotations


class V3PipelineError(RuntimeError):
    """Base class for expected V3 pipeline failures."""


class V3InputError(V3PipelineError):
    """User input or source media is invalid."""


class V3ConfigurationError(V3PipelineError):
    """Application or platform configuration is invalid."""


class V3ExternalToolError(V3PipelineError):
    """An expected external tool operation failed."""


class V3ValidationError(V3PipelineError):
    """A production or media contract was violated."""


class V3ArtifactError(V3PipelineError):
    """A required artifact is missing, corrupt, or inconsistent."""


class V3PackagingError(V3PipelineError):
    """Expected upload/package generation failure."""


class V3ComplianceError(V3PipelineError):
    """Rights, factuality, or release compliance failure."""


__all__ = [
    "V3ArtifactError",
    "V3ComplianceError",
    "V3ConfigurationError",
    "V3ExternalToolError",
    "V3InputError",
    "V3PackagingError",
    "V3PipelineError",
    "V3ValidationError",
]
