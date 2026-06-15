---
name: geocode
description: 使用百度地图或天地图官方 API 进行地理编码（地址/地名 → 经纬度）与逆地理编码（经纬度 → 地址）查询。支持多提供商切换，密钥从环境变量或 .env 文件读取。
---

# 地理编码 Skill（统一版）

## 适用范围

- 地理编码：中文地址 / 地名 / POI → 经纬度
- 逆地理编码：经纬度 → 结构化地址
- 支持提供商：百度地图（baidu）、天地图（tianditu）

## 文件

- `SKILL.md`：本说明
- `geocode.py`：统一调用脚本
- `.env.example`：环境变量示例
- `.gitignore`：忽略本地 `.env`

不要在文档中写死本机绝对路径。使用本 Skill 时，先定位 Skill 目录，再用相对路径执行脚本。

---

## 密钥配置

密钥不通过命令行参数传入，避免泄露到 shell history 或进程列表。

推荐在项目根目录创建 `.env`，可同时配置两个提供商：

```env
BAIDU_AK=你的百度地图Key
TIANDITU_TK=你的天地图Key
```

也可以直接设置进程环境变量：

```bash
export BAIDU_AK="你的百度地图Key"
export TIANDITU_TK="你的天地图Key"
```

脚本读取优先级：

1. 已存在的环境变量（`BAIDU_AK` / `TIANDITU_TK`）
2. 当前工作目录及其父目录中的第一个 `.env`
3. 本 Skill 目录下的 `.env`（仅作为本地兜底，不建议提交）

> `.env` 包含密钥，必须被 `.gitignore` 忽略；仓库中只保留 `.env.example`。

---

## 快速使用

先进入本 Skill 目录，或用脚本相对路径调用：

```bash
cd .agents/skills/geocode
```

### 地理编码：地址 → 经纬度

默认使用天地图：

```bash
python3 geocode.py geocode "北京市海淀区上地十街10号"
```

切换为百度地图：

```bash
python3 geocode.py -p baidu geocode "北京市海淀区上地十街10号"
```

### 逆地理编码：经纬度 → 地址

默认使用天地图：

```bash
python3 geocode.py reverse 113.850442 30.200003
```

切换为百度地图：

```bash
python3 geocode.py -p baidu reverse 116.308149542 40.056885091
```

> 百度地图逆地理编码接口要求 `location=lat,lng`，脚本内部会自动转换，CLI 仍保持 `reverse <lon> <lat>` 的直觉顺序。

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--provider` / `-p` | 提供商：`baidu` 或 `tianditu`（默认 `tianditu`） |
| `geocode <address>` | 地址/地名 → 经纬度 |
| `reverse <lon> <lat>` | 经纬度 → 地址 |

---

## API 说明

### 百度地图

- 地理编码：`https://api.map.baidu.com/geocoding/v3`
- 逆地理编码：`https://api.map.baidu.com/reverse_geocoding/v3`
- 参数：`address` / `location` + `output=json` + `ak`
- 坐标系：BD09

成功响应示例（地理编码）：

```json
{
  "status": 0,
  "result": {
    "location": {
      "lng": 116.308149542,
      "lat": 40.056885091
    },
    "precise": 1,
    "confidence": 80,
    "comprehension": 100,
    "level": "门址"
  }
}
```

### 天地图

- 接口：`http://api.tianditu.gov.cn/geocoder`
- 地理编码参数：`ds`（URL 编码后的 JSON：`{"keyWord":"地址"}`） + `tk`
- 逆地理编码参数：`postStr`（URL 编码后的 JSON：`{"lon":..., "lat":..., "ver":1}`） + `type=geocode` + `tk`

成功响应示例（地理编码）：

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

---

## Agent 执行清单

1. 判断用户需求：地址转坐标，还是坐标转地址。
2. 选择提供商（默认 `tianditu`，用户可指定 `baidu`）。
3. 确认对应密钥可用（`TIANDITU_TK` 或 `BAIDU_AK`）；没有则要求用户提供并写入项目根目录 `.env`。
4. 使用 `python3 geocode.py -p <provider> geocode <地址>` 或 `python3 geocode.py -p <provider> reverse <lon> <lat>`。
5. 检查返回状态：
   - 百度地图：`status == 0`
   - 天地图：`status == "0"`
6. 提取结果：
   - 百度地图地理编码：`result.location.lng`、`result.location.lat`
   - 百度地图逆地理编码：`result.formatted_address`、`result.addressComponent`
   - 天地图地理编码：`location.lon`、`location.lat`
   - 天地图逆地理编码：`result.formatted_address`、`result.addressComponent`

## 百度地图状态码速查

| status | 含义 |
|--------|------|
| 0 | 正常 |
| 1 | 服务器内部错误 |
| 2 | 请求参数非法 |
| 3 | 权限校验失败（AK 无效 / 未授权 / 配额超限） |
| 4 | 配额超限 |
| 5 | AK 不存在或被封禁 |
| 101 | 服务禁用 |
| 102 | 不通过白名单或安全校验 |
| 200 | 无请求权限 |
| 300 | 无请求权限（配额超限） |

## 常见问题

| 问题 | 处理 |
|------|------|
| 未找到密钥 | 检查环境变量或 `.env` 是否包含对应提供商的密钥 |
| 百度 status=3 / 5 | AK 无效或权限不足；检查 AK 是否正确，是否已开通地理编码服务 |
| 百度 status=4 / 300 | 配额超限；考虑升级账号或更换 AK |
| 天地图返回参数格式错误 | 检查 `ds` / `postStr` 是否 URL 编码；脚本内部已自动编码 |
| 返回无结果 | 增加省市区限定，使用更精确地址 |
| 经纬度超范围 | 经度 `[-180,180]`，纬度 `[-90,90]` |
| 坐标系说明 | 百度地图返回 BD09；天地图返回 WGS84；跨源对比时注意转换 |

## 参考链接

- 百度地图地理编码文档：https://lbsyun.baidu.com/faq/api?title=webapi/guide/webservice-geocoding
- 百度地图开发者控制台：https://lbsyun.baidu.com/apiconsole/key
- 天地图地理编码服务文档：http://lbs.tianditu.gov.cn/server/geocoding.html
- 天地图开发者控制台：https://console.tianditu.gov.cn/
