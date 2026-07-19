"""Domain vocabulary for spot intake: fish species lexicon, place-name hints, keyword categories.

This module is the single source of truth for the project's domain lexicons.
Both the collector and the exporter import from here — never copy these tables.
"""

from __future__ import annotations

# Fallback tokens when the local OpenAI-compatible LLM is unavailable.
# The primary extractor asks the LLM to return place names from title/description/page text.
PLACE_PATTERNS = [
    "野芷湖公园", "野芷湖", "东荆河", "倒水河", "汉江", "长江", "府河", "滠水河",
    "汤逊湖", "梁子湖", "后官湖", "严西湖", "严东湖", "金银湖", "墨水湖", "南湖",
    "蔡甸江滩", "汉口江滩", "武昌江滩", "联丰村", "走马岭水厂",
]

COMMENT_PLACE_HINTS = [
    "江", "河", "湖", "水库", "江滩", "闸", "桥", "泵站", "水厂", "码头", "村", "湾", "港", "沟", "渠", "公园",
]

# ---------------------------------------------------------------------------
# 精度分级 (precision) lexicons — see CONTEXT.md. Classification itself lives
# in extract.py (classify_place_name / refine_precision); these are the tables.
# ---------------------------------------------------------------------------

# Anchorless generics: no navigable anchor, no information ("在凼子钓" ≈ "在塘里钓").
# 泛词+地标（"某某停车场附近"）不归这里管——LLM 提取的应是锚点本身。
GENERIC_PLACE_BLOCKLIST = {
    "凼子", "塘子", "河边", "江边", "湖边", "水库边", "附近", "钓点", "野钓", "钓场", "鱼池",
}

# Main-stem rivers: a bare name carries no actionable location (长江/汉江).
# 支流（府河、东荆河、滠水…）不在此列——光秃支流名是 segment，不是 reject。
MAIN_STEM_WATER_BODIES = {"长江", "汉江"}

# Administrative suffixes → reject, unless the name ends with a real-place
# exception (吹笛景区 is a scenic area, not a district).
ADMIN_SUFFIXES = ("区", "县")
ADMIN_EXCEPTION_SUFFIXES = ("景区", "湖区", "园区", "度假区", "旅游区", "保护区", "示范区")

# Coarse-but-meaningful area suffixes → segment (村居/街道 granularity has value).
SEGMENT_SUFFIXES = ("街道", "镇", "乡", "村", "社区", "片区")

# Linear water bodies cannot be pinned to a point → segment.
# (湖/水库/塘/湾 are compact bodies — a geocoder point on them is meaningful.)
LINEAR_WATER_SUFFIXES = ("河", "江", "港", "沟", "渠", "溪", "汊", "水")

COMMENT_NOISE = {"全部评论", "留下你的精彩评论吧", "大家都在搜：", "分享", "回复", "作者", "加载中", "关注", "推荐视频"}

COMMENT_KEYWORD_CATEGORIES = {
    "place": "钓点/地名",
    "fish": "鱼种",
    "fish_condition": "鱼情/口况",
    "water_condition": "水情",
    "access": "交通/停车/到达难度",
    "restriction": "禁钓/收费/管理/风险",
    "bait_method": "饵料/钓法/装备",
    "quality": "总体评价/建议",
}

COMMENT_KEYWORD_CATEGORY_ALIASES = {
    "地点": "place",
    "地名": "place",
    "钓点": "place",
    "鱼": "fish",
    "鱼种": "fish",
    "鱼情": "fish_condition",
    "口况": "fish_condition",
    "水情": "water_condition",
    "交通": "access",
    "停车": "access",
    "限制": "restriction",
    "禁钓": "restriction",
    "风险": "restriction",
    "饵料": "bait_method",
    "钓法": "bait_method",
    "装备": "bait_method",
    "评价": "quality",
    "质量": "quality",
}

LLM_TEXT_NOISE = {
    "读屏标签已关闭", "精选", "推荐", "搜索", "关注", "朋友", "我的", "直播", "放映厅", "短剧", "小游戏",
    "下载抖音精选", "播放", "进入全屏H", "网页全屏Y", "截图", "小窗模式U", "字幕", "不 开启", "不开启",
    "稍后再看L", "倍速", "高清 1080P", "高清 720P", "智能", "清屏", "清屏J", "连播", "自动连播K",
    "听抖音", "重播", "举报", "推荐视频", "点击按住可拖动视频", "3s 后播放", "3s 后播放下一个视频",
    "3s 后循环播放当前视频", "全部评论", "留下你的精彩评论吧",
}

LLM_TEXT_KEEP_HINTS = [
    "#", "钓", "鱼", "江", "河", "湖", "水库", "江滩", "闸", "桥", "泵站", "水厂", "码头", "村", "湾", "港",
    "章节要点", "引言", "鱼情", "钓获", "发布时间", "作者", "粉丝", "获赞",
]

# Fish species aliases commonly appearing in Wuhan fishing videos.
# Keys are canonical names persisted into DB; values are surface forms used by
# rules and by the LLM normalizer. Keep longer/more specific aliases first
# where ambiguity exists (e.g. 青尾鲴 before 青尾).
FISH_PATTERNS = {
    "黄尾鲴": ["黄尾鲴", "黄尾", "黄片", "黄尾巴"],
    "青尾鲴": ["青尾鲴", "青尾鲴鱼", "青尾", "青尾巴"],
    "鲫鱼": ["工程鲫", "板鲫", "大板鲫", "斤鲫", "土鲫", "野鲫", "鲫鱼"],
    "鲤鱼": ["大鲤鱼", "巨鲤", "拐子", "鲤鱼"],
    "草鱼": ["草鱼", "草混", "草棒"],
    "鳊鱼": ["武昌鱼", "鳊鱼"],
    "翘嘴": ["翘嘴红鲌", "大翘嘴", "翘壳", "翘嘴", "白鱼"],
    "罗非鱼": ["罗非鱼", "非洲鲫", "罗非"],
    "鲢鳙": ["花鲢", "白鲢", "胖头鱼", "大头鱼", "鲢鳙", "鲢鱼", "鳙鱼"],
    "鲮鱼": ["土鲮", "麦鲮", "泰鲮", "小鲮鱼", "鲮鱼"],
    "黑鱼": ["乌鳢", "乌鱼", "财鱼", "黑鱼"],
    "鳜鱼": ["桂鱼", "季花鱼", "鳜鱼"],
    "黄颡鱼": ["黄颡鱼", "黄骨鱼", "昂刺鱼", "黄辣丁", "黄鸭叫", "黄骨", "黄颡"],
    "鲶鱼": ["鲶鱼", "塘鲺", "胡子鲶"],
    "鲈鱼": ["鲈鱼", "海鲈", "七星鲈"],
    "红尾": ["红尾", "红尾鱼"],
    "马口": ["马口", "马口鱼"],
    "白条": ["白条", "餐条", "参条", "蓝刀"],
}
