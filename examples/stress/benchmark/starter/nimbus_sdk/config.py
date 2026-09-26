"""Client configuration values. Precedence is implemented by the application."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NimbusConfig:
    api_key: str
    base_url: str = "https://api.nimbus.local"
    timeout_seconds: float = 10.0
    api_version: str = "2024-06-01"
