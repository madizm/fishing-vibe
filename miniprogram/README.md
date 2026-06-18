# 微信小程序版 - 武汉钓鱼钓点地图

## 工程结构

```
miniprogram/
├── app.js               # App 入口
├── app.json             # 全局配置
├── app.wxss             # 全局样式
├── sitemap.json
├── pages/
│   └── index/
│       ├── index.js     # 页面逻辑
│       ├── index.json   # 页面配置
│       ├── index.wxml   # 页面模板
│       └── index.wxss   # 页面样式
└── utils/
    ├── data.js          # 数据处理工具
    └── fishing-spots.json  # 118个钓点数据
```

## 与 Web 版的差异

| 功能 | Web 版 | 小程序版 |
|------|-------|---------|
| 地图底图 | 天地图（TK 密钥） | 腾讯地图（内置，无需密钥） |
| 地图 API | `T.Map` / `T.Marker` | `<map>` 组件 + `wx.createMapContext` |
| 存储 | `localStorage` | `wx.setStorageSync`（本版无需，JSON 直接 require） |
| 数据加载 | `fetch('./fishing-spots.json')` | `require('./fishing-spots.json')` |
| 样式单位 | `px / vw / vh` | `rpx`（750rpx = 屏幕宽度） |
| DOM 操作 | `innerHTML / addEventListener` | `setData` 驱动 + `bind*` 事件 |

## 使用方式

1. 打开**微信开发者工具**
2. 新建项目，选择**导入已有项目**，目录指向本文件夹（`miniprogram/`）
3. 填入你的 AppID（无 AppID 可选「测试号」）
4. 编译预览即可

## 注意事项

- 小程序 `<map>` 组件默认使用腾讯地图，**不支持天地图底图**，但标记点数据完整保留
- 如需卫星图，已内置「切换卫星图」按钮（使用腾讯卫星底图）
- 抖音视频链接无法直接打开（小程序限制），已改为「复制链接」功能
- 发布上线需在微信公众平台配置合法域名（本地开发无需配置）
