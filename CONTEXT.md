# CONTEXT.md

Domain glossary for fishing-vibe. Use these terms in code, issues, and reviews — don't drift to synonyms.

## Terms

- **钓点 (fishing spot)** — the central entity. A geocodable place (river, lake, reservoir, riverbank, bridge, sluice, park, village…) where people fish, extracted from Douyin videos and their comments. Persisted in `fishing_spots`, exported as GeoJSON for the web and miniprogram maps.
- **收录 (spot intake)** — the pipeline that turns a Douyin video URL or search keyword into persisted 钓点: extract page text/comments/transcripts → extract place candidates and 鱼种 → geocode (with POI auto-correction) → score quality → write to SQLite. Implemented by the `spot_intake` package.
- **鱼种 (fish species)** — fish mentioned in a video or comment. Canonical names (e.g. 黄尾鲴, 鲫鱼, 翘嘴) come from the single lexicon in `spot_intake/vocabulary.py` (`FISH_PATTERNS`); surface forms (工程鲫, 大板鲫, 翘壳…) normalize to canonical names. Never copy the lexicon.
- **质量分 (quality score)** — 0–1 score on a 钓点, normalized from an LLM's 1–5 rating of what comments say about fish activity, access, and restrictions. Confidence-weighted when aggregating groups; `null` means "no information", not "bad".
- **评论关键词 (comment keywords)** — short categorized labels extracted from comments (e.g. 有口, 空军, 禁钓, 停车方便). Categories are the fixed enum in `COMMENT_KEYWORD_CATEGORIES`.
- **转写 (transcription)** — turning a video's audio track into text. A processing step, not an entity.
- **转写文本 (transcript)** — the text produced by 转写. A first-class text source for 收录, on par with page text and comments: it is cleaned, sent through the same LLM extraction and vocabulary normalization, and its contributions are marked with `source_type = "transcript"`.
- **转写摘要 (transcript summary)** — a human-readable LLM summary of a transcript, plus `extras` (钓法/饵料, 渔获, 出钓时间…). Video-dimensional, persisted with the transcript, for people — never normalized into 钓点 data.
- **地名候选 (place candidates)** — extracted place names before geocoding; a candidate becomes a 钓点 only after the geocoder confirms it.
- **坐标系 (coordinate systems)** — WGS84 is the canonical storage/exchange CRS (web/Tianditu). GCJ-02 is produced at build time for the WeChat miniprogram. BD09 appears only at the Baidu geocode API boundary.

## Conventions

- Pure extraction/normalization logic lives in `spot_intake/extract.py` and is side-effect free; lexicons live in `spot_intake/vocabulary.py`.
- The LLM is an extraction tool, not a source of truth: everything it returns is normalized against the vocabulary before persistence.
