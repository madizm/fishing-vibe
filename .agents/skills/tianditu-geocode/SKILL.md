---
name: tianditu-geocode
description: 使用天地图（Tianditu）官方 API 进行地理编码（地址/地名 → 经纬度）与逆地理编码（经纬度 → 地址）查询。密钥从 TIANDITU_TK 环境变量或 .env 文件读取。
---

# 天地图地理编码 Skill

## 适用范围

- 地理编码：中文地址 / 地名 / POI → 经纬度
- 逆地理编码：经纬度 → 结构化地址
- 中文地址解析、POI 坐标获取、坐标反查地址

## 文件

- `SKILL.md`：本说明
- `tianditu_geocode.py`：调用脚本
- `.env.example`：环境变量示例
- `.gitignore`：忽略本地 `.env`

不要在文档中写死本机绝对路径。使用本 Skill 时，先定位 Skill 目录，再用相对路径执行脚本。

---

## 密钥配置

密钥不通过命令行参数传入，避免泄露到 shell history 或进程列表。

推荐在项目根目录创建 `.env`：

```env
TIANDITU_TK=你的天地图Key
```

也可以直接设置进程环境变量：

```bash
export TIANDITU_TK="你的天地图Key"
```

脚本读取优先级：

1. 已存在的环境变量 `TIANDITU_TK`
2. 当前工作目录及其父目录中的第一个 `.env`
3. 本 Skill 目录下的 `.env`（仅作为本地兜底，不建议提交）

> `.env` 包含密钥，必须被 `.gitignore` 忽略；仓库中只保留 `.env.example`。

---

## 快速使用

先进入本 Skill 目录，或用脚本相对路径调用：

```bash
cd .agents/skills/tianditu-geocode
```

### 地理编码：地址 → 经纬度

```bash
python3 tianditu_geocode.py geocode "武汉东荆河"
```

### 逆地理编码：经纬度 → 地址

```bash
python3 tianditu_geocode.py reverse 113.850442 30.200003
```

---

## API 说明

接口地址：

```text
http://api.tianditu.gov.cn/geocoder
```

### 一、地理编码（地址 → 经纬度）

请求参数：

| 参数 | 必填 | 说明 |
|------|------|------|
| `ds` | 是 | URL 编码后的 JSON，如 `{"keyWord":"武汉东荆河"}` |
| `tk` | 是 | 天地图密钥，从 `TIANDITU_TK` 读取 |

curl 示例：

```bash
set -a; source .env; set +a
DS=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("{\"keyWord\":\"武汉东荆河\"}"))')
curl -s "http://api.tianditu.gov.cn/geocoder?ds=${DS}&tk=${TIANDITU_TK}"
```

成功响应示例：

```json
{
  "msg": "ok",
  "location": {
    "score": 100,
    "level": "兴趣点",
    "lon": "113.850442",
    "lat": "30.200003",
    "keyWord": "武汉东荆河"
  },
  "status": "0"
}
```

### 二、逆地理编码（经纬度 → 地址）

请求参数：

| 参数 | 必填 | 说明 |
|------|------|------|
| `postStr` | 是 | URL 编码后的 JSON，如 `{"lon":113.850442,"lat":30.200003,"ver":1}` |
| `type` | 是 | 固定为 `geocode` |
| `tk` | 是 | 天地图密钥，从 `TIANDITU_TK` 读取 |

curl 示例：

```bash
set -a; source .env; set +a
POST_STR=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("{\"lon\":113.850442,\"lat\":30.200003,\"ver\":1}"))')
curl -s "http://api.tianditu.gov.cn/geocoder?postStr=${POST_STR}&type=geocode&tk=${TIANDITU_TK}"
```

---

## Agent 执行清单

1. 判断用户需求：地址转坐标，还是坐标转地址。
2. 确认 `TIANDITU_TK` 可用；没有则要求用户提供 Key，并写入项目根目录 `.env`。
3. 优先使用 `python3 tianditu_geocode.py geocode <地址>` 或 `python3 tianditu_geocode.py reverse <lon> <lat>`。
4. 检查返回 `status` 是否为 `0`。
5. 地理编码提取 `location.lon`、`location.lat`；逆地理编码提取 `result.formatted_address` 与 `result.addressComponent`。
6. 若返回 `参数格式错误`，优先检查 JSON 是否已 URL 编码；脚本内部已自动编码，手写 curl 时尤其注意。

## 常见问题

| 问题 | 处理 |
|------|------|
| 未找到密钥 | 检查环境变量或 `.env` 是否包含 `TIANDITU_TK=...` |
| 参数格式错误 | 检查 `ds` / `postStr` 是否 URL 编码 |
| 返回无结果 | 增加省市区限定，使用更精确地址 |
| 密钥无效 | 更换天地图 Key |
| 经纬度超范围 | 经度 `[-180,180]`，纬度 `[-90,90]` |

## 参考链接

- 天地图地理编码服务文档：http://lbs.tianditu.gov.cn/server/geocoding.html
- 天地图开发者控制台：https://console.tianditu.gov.cn/
