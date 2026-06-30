"""Tool-result construction — the ``structuredContent`` and domain-error contract.

The spec requires every tool result to carry ``structuredContent`` as a top-level
OBJECT (MCP forbids a bare array), and unknown fields to be preserved/round-tripped
(the projection's Claunker extensions — ``gate_status``, ``badge`` — must survive).
Returning a fully-formed ``CallToolResult`` (rather than letting the SDK infer an
output schema from the return annotation) gives us exactly that: the object passes
through verbatim, with nothing stripped and no schema to violate.

Domain errors ride in the result too (``isError: true`` + ``{code, message, meta}``)
— distinct from transport/auth failures, which are HTTP/JSON-RPC layer.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import mcp.types as types

# Domain-error codes used by the tool surface. ``payload_too_large`` is the read
# tools' only realistic error; the other three are the ``escalation_resolve``
# mutation's validation / authorization / lookup codes. These now CONFORM to the
# spec's reserved §Errors vocabulary — ``validation_failed`` / ``unauthorized`` /
# ``not_found`` — rather than the gRPC-style ``invalid_argument`` / ``forbidden``
# that previously sat alongside it (the divergence flagged for the v2 error-code
# reconciliation is resolved).
PAYLOAD_TOO_LARGE = "payload_too_large"
NOT_FOUND = "not_found"
VALIDATION_FAILED = "validation_failed"
UNAUTHORIZED = "unauthorized"


def _content(obj: Dict[str, Any]) -> List[types.TextContent]:
    """Mirror the structured object into a text block for non-structured clients."""
    return [types.TextContent(type="text", text=json.dumps(obj, default=str))]


def ok_result(obj: Dict[str, Any]) -> types.CallToolResult:
    """A successful result: ``structuredContent`` is the top-level object verbatim
    (extension fields intact), mirrored in text content. ``isError`` is false."""
    return types.CallToolResult(content=_content(obj), structuredContent=obj, isError=False)


def domain_error_result(
    code: str, message: str, meta: Optional[Dict[str, Any]] = None
) -> types.CallToolResult:
    """A domain error in the tool result: ``isError: true`` with the spec's
    ``{code, message, meta}`` payload (in both structuredContent and text)."""
    payload = {"code": code, "message": message, "meta": meta or {}}
    return types.CallToolResult(content=_content(payload), structuredContent=payload, isError=True)
