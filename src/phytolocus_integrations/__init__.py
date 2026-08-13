"""Optional, non-authoritative integrations for :mod:`phytolocus`.

The integration package converts external transport results and local artifact
bytes into explicit receipts.  It does not close identity, phasing, expression,
function, causality, safety, or target-selection gates.
"""

from .artifacts import ArtifactReceiptError, artifact_receipt, study_receipt_from_artifact
from .tooluniverse import (
    TOOLUNIVERSE_PIN,
    SourceContractError,
    ToolUniverseAdapter,
    audit_collection,
    receipt_from_call,
    study_receipt_from_call,
)

__all__ = [
    "TOOLUNIVERSE_PIN",
    "ArtifactReceiptError",
    "SourceContractError",
    "ToolUniverseAdapter",
    "artifact_receipt",
    "audit_collection",
    "receipt_from_call",
    "study_receipt_from_artifact",
    "study_receipt_from_call",
]
