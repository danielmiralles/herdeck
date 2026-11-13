"""Shared test helpers (stdlib only)."""
import json
from pathlib import Path
from typing import Any, Dict

FROZEN_NOW = 1_800_000_000.0
FIXTURES = Path(__file__).parent / "fixtures"


class FakeHub:
    def __init__(self) -> None:
        self.sources: Dict[str, Any] = {}
        self.calls = 0

    def update_source(self, name: str, data: Dict[str, Any]) -> None:
        self.sources[name] = data
        self.calls += 1


def load_fixture(name: str) -> Any:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)
