# 视频转写：transcript 作为收录的一等文本来源

收录 pipeline 新增第三类文本来源：视频音轨经 ASR 转写出的 **转写文本 (transcript)**，与页面文本、评论并列，走同一套 LLM 提取 + 词汇归一化 + geocode（含 POI 自动纠错）后落库，`source_type = "transcript"`，置信度与 `video_text` 同档（0.9/0.7）。口播是钓鱼视频中钓点信息密度最高的来源（"今天在府河用蚯蚓上了三条翘嘴"），而页面文本常只有标题和话题标签。

持久化为 `video_transcripts` 表（`video_id UNIQUE`，随 `videos` 级联删除）：transcript 文本、音频文件路径（`downloads/{vid}.m4a`，mp4 即删）、ASR raw response 路径、四态 `status`、以及 LLM 对人读的产出 `summary` / `extras_json`（视频维度，永不归一化进钓点数据；展示层另见 issue #1）。

**status 四态**（回填实战后修订，原为三态）：`ok`、`error`（瞬时失败，回填重试）、两个终态不再重试——`no_speech`（纯 BGM 无人声）与 `unavailable`（视频已删/私密/被处理，页面重定向到精选流，`Browser` 抛 `VideoUnavailable`）。

## Decisions

1. **`Browser` port 扩展 `download_audio`，不设独立 `AudioSource` port。** 关键约束：抖音音频不存在 API 直取这一替换轴——取直链必须开浏览器会话，未来也不会变。单 port 可对同一视频复用浏览器会话；独立 port 是不会有实现者的抽象。（曾考虑独立 port 以隔离下载/转写的失败域，被此约束否决。）

2. **LLM 多次单任务调用，不用一次复合调用。** transcript 的提取 = 复用 `extract_places` / `extract_fish_species` + 新增 `summarize_transcript`（summary/extras 合一次调，因描述性产出容错高）。取舍依据：可能使用低成本模型，弱模型在复合指令 + 长 JSON 输出上不可靠；成本优先于调用次数。

3. **转写失败非致命。** 三类文本来源互为冗余：转写失败只记 `status='error'`，视频凭其余来源照常收录，回填脚本（`scripts/backfill_transcripts.py`，断点续跑）按 `status='error'` 重试。`no_speech` 与 `unavailable` 是终态，永不重试。`no_speech` 判定启发式（空文本/语气词）封在 Mimo 适配器内；`unavailable` 判定（页面重定向）封在浏览器适配器内。

4. **ASR 谐音错字的两道防线。** 首选：transcript 提取 prompt 下谐音纠正指令——纠正是语境理解任务，提取时语境最全（独立 prompt 变体，不污染页面文本 prompt）。兜底：geocoder 已有的低置信度 POI 自动纠错 + `--region` 城市限制。不加独立的"地名校正"LLM 调用（弱模型干细活更差，且白增调用）；若某类错字系统性出现，再往 `vocabulary.py` 加谐音词表。

## Consequences

- `collect_video` 单视频耗时显著增加（浏览器取链 + 下载 + ffmpeg + ASR），用 `IntakeOptions.include_transcript` 开关和批量限速消化，不改触发策略。
- `max_video_places` 对各来源独立应用，不共享预算。
- Mimo ASR 逻辑从 `tools/transcribe_mimo_asr.py` 迁入 `spot_intake/adapters/mimo_asr.py`；工具脚本保留为薄 CLI 壳供手动调试。
- 124 条历史视频由回填脚本补齐，完成后全库三来源覆盖一致。
