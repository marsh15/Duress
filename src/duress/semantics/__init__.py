"""Execution-semantics identity: ``exec@1#sha256(canonical spec)``."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from ..models import canonical_sha256

SPEC_NAME = "exec_v1.json"


@lru_cache(maxsize=1)
def spec() -> dict[str, Any]:
    resource = resources.files("duress.semantics").joinpath(SPEC_NAME)
    return json.loads(resource.read_text())


def digest() -> str:
    return canonical_sha256(spec())


def identity() -> str:
    document = spec()
    return f"{document['id']}@{document['version']}#{digest()}"


def clause(clause_id: str) -> dict[str, Any]:
    for item in spec()["clauses"]:
        if item["id"] == clause_id:
            return item
    raise KeyError(clause_id)


def supports(identifier: str, version: int, expected_digest: str) -> bool:
    document = spec()
    return (
        document["id"] == identifier
        and document["version"] == version
        and digest() == expected_digest
    )