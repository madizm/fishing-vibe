# 钓鱼钓点查询软件实施方案

## 1. 项目目标

通过 OpenCLI 搜索抖音「武汉钓鱼」相关视频，提取视频标题、URL、发布时间与地名信息，再调用天地图地理编码获取经纬度，形成可查询、可地图展示的钓点数据库。

## 2. 目标数据字段

| 字段 | 说明 |
|---|---|
| platform | 数据平台，固定为 douyin |
| keyword | 搜索关键词，如「武汉钓鱼」 |
| title | 视频标题 / 文案摘要 |
| url | 视频 URL |
| author | 作者 |
| publish_time | 发布时间 |
| raw_text | 页面提取的原始文本 |
| place_name | 识别出的地名 / 钓点 |
| query_name | 地理编码查询词，如「武汉东荆河」 |
| longitude | 经度 |
| latitude | 纬度 |
| geocode_score | 天地图匹配分 |
| geocode_level | 天地图匹配级别 |
| confidence | 钓点可信度 |
| collected_at | 采集时间 |

## 3. 总体流程

```text
1. 使用 OpenCLI 以「武汉钓鱼」搜索抖音视频
2. 获取视频列表和 URL
3. 使用 opencli-browser 打开视频 URL
4. 提取视频发布时间、标题、文案、地名相关信息
5. 使用 tianditu-geocode 根据地名查询经纬度
6. 保存标题、发布时间、地名、经纬度等结构化数据
7. 后续支持地图查询、去重、热度排序
```

## 4. 技术路线

### 4.1 OpenCLI 搜索

优先使用已有抖音 adapter：

```bash
opencli douyin search "武汉钓鱼" --limit 10 -f json
```

若 adapter 不稳定，则退回 `opencli browser`：

```bash
opencli browser douyin-fishing open "https://www.douyin.com/search/武汉钓鱼"
opencli browser douyin-fishing state
opencli browser douyin-fishing network
```

### 4.2 视频详情提取

对每个视频 URL：

```bash
opencli browser douyin-video open "<video_url>"
opencli browser douyin-video state
opencli browser douyin-video extract
opencli browser douyin-video network
```

优先级：

```text
network 接口数据 > 页面结构化数据 > DOM 文本 > 截图/OCR
```

### 4.3 地名识别

从标题、文案、定位标签中提取候选地名。

规则：

- 页面 POI / 定位标签优先
- 标题中的地名次之
- 文案中的地名再次
- 评论中的地名作为后续增强
- 模糊地名自动补城市前缀，例如「东荆河」→「武汉东荆河」

### 4.4 天地图地理编码

```bash
python .agents/skills/tianditu-geocode/tianditu_geocode.py geocode "武汉东荆河"
```

要求项目根目录存在：

```env
TIANDITU_TK=你的天地图Key
```

### 4.5 循环频率控制

批量采集必须低频串行执行，避免触发平台风控或登录态失效。

默认策略：

- 单进程、单 browser session、串行处理视频详情页
- 每个视频详情页处理完成后随机暂停 `8-20` 秒
- 首轮建议 `--limit 3` 小批量验证，稳定后再扩大到 `10-30`
- 不做并发打开、不做多 session 并行、不连续快速刷新搜索页
- 触发 `AUTH_REQUIRED`、验证码、页面空白或连续失败时立即停止，重新确认登录态后再继续

脚本参数：

```bash
python scripts/collect_douyin_fishing_spots.py \
  --keyword "武汉钓鱼" \
  --limit 3 \
  --delay-min 8 \
  --delay-max 20
```

如需更保守，可调整为：

```bash
python scripts/collect_douyin_fishing_spots.py --limit 10 --delay-min 30 --delay-max 60
```

### 4.6 数据存储

首版使用 SQLite。

```sql
CREATE TABLE videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT DEFAULT 'douyin',
  keyword TEXT,
  title TEXT,
  url TEXT UNIQUE,
  author TEXT,
  publish_time TEXT,
  raw_text TEXT,
  collected_at TEXT
);

CREATE TABLE fishing_spots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER,
  place_name TEXT,
  query_name TEXT,
  longitude REAL,
  latitude REAL,
  geocode_score INTEGER,
  geocode_level TEXT,
  confidence REAL,
  source_text TEXT,
  created_at TEXT,
  FOREIGN KEY(video_id) REFERENCES videos(id)
);
```

## 5. 实施顺序与验收标准

| 阶段 | 任务 | 验收标准 | 状态 |
|---|---|---|---|
| 0 | 环境预检 | OpenCLI doctor 通过，确认 douyin adapter 存在 | 已完成 |
| 1 | 搜索「武汉钓鱼」 | 获取至少 1 条视频标题和 URL | 已完成 |
| 2 | 打开单个视频详情页 | 能通过 browser 打开 URL 并读取页面信息 | 已完成 |
| 3 | 提取发布时间和文本 | 得到 title、publish_time、raw_text | 已完成 |
| 4 | 识别地名 | 从文本中得到至少 1 个候选地名 | 已完成 |
| 5 | 天地图地理编码 | 候选地名成功转经纬度 | 已完成 |
| 6 | 数据入库 | SQLite 中保存 video 与 fishing_spot | 已完成 |
| 7 | 批量化 | 对前 N 条视频循环执行流程 | 已编写脚本，验证受抖音登录态拦截 |
| 8 | 频率控制 | 视频详情循环串行执行，并支持随机延迟参数 | 已完成 |

## 6. 当前跑通记录

### 2026-06-13

- 已创建实施方案文档。
- `opencli doctor` 通过：Daemon、Extension、Connectivity 均 OK。
- 已确认 `douyin/search` adapter 存在，字段包括：rank、desc、author、url、plays、likes、comments、shares。
- 已执行 `opencli douyin search "武汉钓鱼" --limit 10 -f json`，成功获取 10 条视频结果。
- 样例已保存到 `data/douyin_search_wuhan_fishing_sample.json`。
- 第一条视频：`https://www.douyin.com/video/7650067373550784123`，标题/文案：`东荆河钓黄尾，半天一盆鱼实战经验分享#户外钓鱼 #黄尾 #钓鱼技巧 #白刺三代 #超牌觉醒`。
- 已使用 `opencli browser douyin-fishing-video open` 打开第一条视频详情页。
- 已使用 `opencli browser douyin-fishing-video extract --chunk-size 8000` 成功提取页面正文。
- 提取结果：
  - 标题：`东荆河钓黄尾，半天一盆鱼实战经验分享#户外钓鱼 #黄尾 #钓鱼技巧 #白刺三代 #超牌觉醒`
  - 作者：`颜主任爱钓鱼`
  - 发布时间：`2026-06-11 17:28`
  - 地名候选：`东荆河`
  - 页面还包含章节摘要：`在东荆河钓黄尾的实战经验分享，包括钓点选择、饵料准备、钓具搭配和作钓技巧。`
- 已执行天地图地理编码：`python .agents/skills/tianditu-geocode/tianditu_geocode.py geocode "武汉东荆河"`。
- 地理编码结果：经度 `113.850442`，纬度 `30.200003`，score `100`，level `兴趣点`。
- 已保存样例结构化记录：`data/fishing_spot_sample.json`。
- 已创建 SQLite 数据库并入库：`data/fishing_spots.sqlite`，当前 `videos=1`、`fishing_spots=1`。
- 已编写批量采集 MVP 脚本：`scripts/collect_douyin_fishing_spots.py`。
- 脚本流程：搜索 → 打开视频 → extract 提取标题/发布时间/文本 → 规则识别地名 → 天地图地理编码 → SQLite 入库。
- 单独验证脚本内部解析能力通过：可从第一条视频识别 `东荆河`、发布时间 `2026-06-11 17:28`，并成功地理编码。
- 批量脚本完整运行时，`opencli douyin search` 出现抖音登录态拦截：`AUTH_REQUIRED: Douyin search results are blocked behind a login wall`。处理方式：需在 Chrome 中确认/刷新抖音登录态，或先执行 `opencli douyin login` 后重试。
- 重试命令：`python scripts/collect_douyin_fishing_spots.py --keyword "武汉钓鱼" --limit 3 --delay-min 8 --delay-max 20`。
- 已为批量脚本增加循环频率控制：默认每条视频处理后随机暂停 `8-20` 秒，可通过 `--delay-min` / `--delay-max` 调整。

## 7. 风险与处理

| 风险 | 处理 |
|---|---|
| 抖音需要登录 | 已实际触发过 `AUTH_REQUIRED`；使用已登录 Chrome，必要时执行 `opencli douyin login` |
| 页面结构变化 | 优先使用 adapter / network 数据，减少 DOM 依赖 |
| 地名不明确 | 增加「武汉」前缀，结合标题、文案、POI 标签判断 |
| 天地图无结果 | 更换更完整查询词，记录失败原因 |
| 触发平台风控 | 控制频率，默认串行处理并在视频间随机暂停 `8-20` 秒；出现登录/验证码/连续失败立即停止 |

## 8. 搜索调用台账

| site | query | count | status |
|---|---|---:|---|
| opencli registry | douyin | 1 | 已确认 douyin/search 存在 |
| douyin | 武汉钓鱼 | 4 | 首次成功获取 10 条视频；后续批量脚本验证时曾触发 AUTH_REQUIRED 登录态拦截 |
