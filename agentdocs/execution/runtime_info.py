"""Persisted execution-runtime provenance.

Runtime choice is not part of the benchmark fingerprint. These records name
the backend and, for Docker, the images that actually ran.
"""

from __future__ import annotations

from dataclasses import dataclass

RUNTIME_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DockerImageInfo:
    """One image that a Docker runtime used."""

    requested_image: str
    image_id: str
    network: str


@dataclass(frozen=True)
class RuntimeInfo:
    """Provenance stored on a completed run. Schema version 1."""

    schema_version: int
    backend: str
    agent: DockerImageInfo | None = None
    verifier: DockerImageInfo | None = None


def local_runtime_info() -> RuntimeInfo:
    """Provenance for the default host-process runtime."""
    return RuntimeInfo(schema_version=RUNTIME_SCHEMA_VERSION, backend="local")


def runtime_document(info: RuntimeInfo) -> dict[str, object]:
    """Serialize runtime provenance. Mounts, env values, and host paths are omitted."""
    payload: dict[str, object] = {
        "schema_version": info.schema_version,
        "backend": info.backend,
    }
    if info.agent is not None:
        payload["agent"] = {
            "requested_image": info.agent.requested_image,
            "image_id": info.agent.image_id,
            "network": info.agent.network,
        }
    if info.verifier is not None:
        payload["verifier"] = {
            "requested_image": info.verifier.requested_image,
            "image_id": info.verifier.image_id,
            "network": info.verifier.network,
        }
    return payload
