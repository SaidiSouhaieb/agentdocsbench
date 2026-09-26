"""Load a documentation-variant experiment.

The benchmark file stays the benchmark definition. This file names the
documentation directories to compare. It does not name an agent, model,
starter, task, verifier, or runtime.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from agentdocs.config.errors import ConfigError
from agentdocs.config.format_validation_error import _format_validation_error
from agentdocs.config.require_directory import _require_directory
from agentdocs.config.resolve_against_config_dir import _resolve_against_config_dir

EXPERIMENT_CONFIG_VERSIONS: frozenset[int] = frozenset({1})


class DocsVariantConfig(BaseModel):
    """One documentation directory to run the benchmark against."""

    model_config = ConfigDict(extra="forbid")

    id: str
    docs: Path

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, variant_id: str) -> str:
        stripped = variant_id.strip()
        if not stripped:
            raise ValueError("Variant id must not be empty.")
        return stripped


class DocsExperimentConfig(BaseModel):
    """Documentation variants for one benchmark. Schema version 1."""

    model_config = ConfigDict(extra="forbid")

    version: int
    reference: str
    variants: list[DocsVariantConfig]

    @field_validator("version")
    @classmethod
    def version_must_be_supported(cls, version: int) -> int:
        if version not in EXPERIMENT_CONFIG_VERSIONS:
            supported = ", ".join(str(item) for item in sorted(EXPERIMENT_CONFIG_VERSIONS))
            raise ValueError(
                f"Unsupported experiment version {version}. "
                f"Supported versions: {supported}."
            )
        return version

    @field_validator("reference")
    @classmethod
    def reference_must_not_be_blank(cls, reference: str) -> str:
        stripped = reference.strip()
        if not stripped:
            raise ValueError("Reference variant must not be empty.")
        return stripped

    @field_validator("variants")
    @classmethod
    def variants_require_two(
        cls, variants: list[DocsVariantConfig]
    ) -> list[DocsVariantConfig]:
        if len(variants) < 2:
            raise ValueError("At least two documentation variants are required.")
        return variants

    @model_validator(mode="after")
    def variants_must_be_consistent(self) -> "DocsExperimentConfig":
        seen: set[str] = set()
        duplicates: list[str] = []
        for variant in self.variants:
            if variant.id in seen and variant.id not in duplicates:
                duplicates.append(variant.id)
            seen.add(variant.id)
        if duplicates:
            joined = ", ".join(repr(variant_id) for variant_id in duplicates)
            raise ValueError(
                f"Duplicate variant id(s): {joined}. Variant ids must be unique."
            )
        if self.reference not in seen:
            raise ValueError(
                f"Reference {self.reference!r} must match exactly one variant id."
            )
        return self


def load_experiment_config(path: str | Path) -> DocsExperimentConfig:
    """Load an experiment file and resolve each docs path.

    Relative docs paths resolve against the directory that contains the
    experiment file, not the process working directory and not the benchmark
    file's directory. Each docs path must already be a directory. Two variants
    may use the same directory.
    """
    experiment_path = Path(path)
    if not experiment_path.exists():
        raise FileNotFoundError(f"Experiment file not found: {experiment_path}")
    if not experiment_path.is_file():
        raise ConfigError(f"Experiment path is not a file: {experiment_path}")
    data = _read_experiment_mapping(experiment_path)
    try:
        loaded = DocsExperimentConfig.model_validate(data)
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise ConfigError(
            f"Invalid experiment configuration in {experiment_path}:\n{details}"
        ) from exc
    base_dir = experiment_path.resolve().parent
    resolved: list[DocsVariantConfig] = []
    for variant in loaded.variants:
        docs = _resolve_against_config_dir(base_dir, variant.docs)
        _require_directory(f"docs for variant {variant.id}", docs)
        resolved.append(variant.model_copy(update={"docs": docs}))
    return loaded.model_copy(update={"variants": resolved})


def _read_experiment_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"Experiment file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(
            f"Experiment file must contain a YAML mapping, not {type(data).__name__}: {path}"
        )
    return data
