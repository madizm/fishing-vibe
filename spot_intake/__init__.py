"""spot_intake — 钓点收录: deep intake module for Douyin fishing-spot collection.

The interface: Intake.collect_video / collect_keyword / extract, with I/O at
four seams (Browser, Searcher, Llm, Geocoder, SpotStore). Pure domain logic
lives in extract.py; lexicons in vocabulary.py.
"""

from spot_intake import extract, vocabulary
from spot_intake.config import Config
from spot_intake.intake import Intake, IntakeOptions, IntakeReport, analyze_comments, build_extraction_report, parse_video

__all__ = [
    "Config",
    "Intake",
    "IntakeOptions",
    "IntakeReport",
    "analyze_comments",
    "build_extraction_report",
    "extract",
    "parse_video",
    "vocabulary",
]
