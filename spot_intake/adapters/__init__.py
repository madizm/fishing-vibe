"""Production adapters for the spot_intake seams."""

from spot_intake.adapters.geocode_skill import GeocodeSkill
from spot_intake.adapters.llm_openai import NullLlm, OpenaiLlm
from spot_intake.adapters.mimo_asr import MimoTranscriber
from spot_intake.adapters.opencli_browser import OpencliBrowser, OpencliDouyinSearch
from spot_intake.adapters.postgis_store import PostgisSpotStore, init_db

__all__ = [
    "GeocodeSkill",
    "MimoTranscriber",
    "NullLlm",
    "OpenaiLlm",
    "OpencliBrowser",
    "OpencliDouyinSearch",
    "PostgisSpotStore",
    "init_db",
]
