"""Build a Nimbus client from a JSON config file. See docs/getting-started.md."""

from pathlib import Path

from nimbus_sdk import NimbusClient


def build_client(config_path: Path) -> NimbusClient:
    raise NotImplementedError("Construct NimbusClient from the JSON file.")
