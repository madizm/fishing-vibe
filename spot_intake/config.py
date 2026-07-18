"""Environment-first configuration for spot intake.

Precedence: explicit constructor/CLI override > environment variable > default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LLM_BASE_URL = "http://100.90.54.85:8080/v1"


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path
    geocode_script: Path
    llm_url: str

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Config":
        root = root or Path(__file__).resolve().parents[1]
        db_path = Path(os.environ["FISHING_VIBE_DB"]) if os.getenv("FISHING_VIBE_DB") else root / "data" / "fishing_spots.sqlite"
        geocode_script = (
            Path(os.environ["GEOCODE_SCRIPT"]) if os.getenv("GEOCODE_SCRIPT") else root / ".agents" / "skills" / "geocode" / "geocode.py"
        )
        llm_url = os.getenv("FISHING_VIBE_LLM_URL") or (
            os.getenv("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/") + "/chat/completions"
        )
        return cls(root=root, db_path=db_path, geocode_script=geocode_script, llm_url=llm_url)
