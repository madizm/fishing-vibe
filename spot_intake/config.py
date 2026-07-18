"""Environment-first configuration for spot intake.

Precedence: explicit constructor/CLI override > environment variable > .env
file > default. Same .env convention as the geocode skill: first .env found
walking up from the cwd, never overriding already-set variables, values
stripped (CRLF-safe).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LLM_BASE_URL = "http://100.90.54.85:8080/v1"
DEFAULT_ASR_MODEL = "mimo-v2.5-asr"


def load_dotenv() -> Path | None:
    """Load the first .env found walking up from the cwd; never overrides
    already-set environment variables. Values are stripped (CRLF-safe).
    Returns the path loaded, if any."""
    for directory in (Path.cwd(), *Path.cwd().parents):
        env_path = directory / ".env"
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
        return env_path
    return None


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path
    geocode_script: Path
    llm_url: str
    downloads_dir: Path
    asr_model: str
    mimo_api_key: str

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Config":
        load_dotenv()
        root = root or Path(__file__).resolve().parents[1]
        db_path = Path(os.environ["FISHING_VIBE_DB"]) if os.getenv("FISHING_VIBE_DB") else root / "data" / "fishing_spots.sqlite"
        geocode_script = (
            Path(os.environ["GEOCODE_SCRIPT"]) if os.getenv("GEOCODE_SCRIPT") else root / ".agents" / "skills" / "geocode" / "geocode.py"
        )
        llm_url = os.getenv("FISHING_VIBE_LLM_URL") or (
            os.getenv("OPENAI_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/") + "/chat/completions"
        )
        downloads_dir = (
            Path(os.environ["FISHING_VIBE_DOWNLOADS_DIR"]) if os.getenv("FISHING_VIBE_DOWNLOADS_DIR") else root / "downloads"
        )
        asr_model = os.getenv("FISHING_VIBE_ASR_MODEL", DEFAULT_ASR_MODEL)
        mimo_api_key = os.getenv("MIMO_API_KEY", "")
        return cls(
            root=root,
            db_path=db_path,
            geocode_script=geocode_script,
            llm_url=llm_url,
            downloads_dir=downloads_dir,
            asr_model=asr_model,
            mimo_api_key=mimo_api_key,
        )
