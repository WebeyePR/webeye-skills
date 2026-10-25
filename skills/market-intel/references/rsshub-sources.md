# RSSHub 自部署服务：部署、渠道清单与维护知识

> 2026-10-24 全面核查。本技能已有源走本地 RSSHub（如 36 氪快讯），
> 另有多个已核实可用渠道登记在 `sources.json`（`enabled: false` 备用，要用改 `true`）。
> **改动前先读本文，不要凭记忆猜路由。**

## 一、部署现状与地址管理

| 项 | 说明 |
|---|---|
| 部署方式 | Docker 官方镜像 `diygod/rsshub:latest`，容器名 `rsshub`，端口 `1200` |
| 地址 | **`http://127.0.0.1:1200`**（登记于 `sources.json` 的 `settings.rsshub_base`） |
| 改地址 | **只改 `settings.rsshub_base` 一处**——所有 `rsshub://` 前缀的源自动拼装到新地址（`pipeline.load_sources` 实现） |
| 缓存 | 默认 3600s（1 小时），即快讯类时效下限约 1 小时 |
| 时区 | TZ=Asia/Shanghai |

源 URL 写法约定：

```json
{"id": "cls-telegraph", "url": "rsshub://cls/telegraph"}
```

`rsshub://<路由路径>` 会在加载时替换为 `{rsshub_base}/{路由路径}`。
部署迁移（换机器/换端口/换公网服务）只改 `settings.rsshub_base`，不逐条改源。

## 二、部署与版本维护（AI 执行时按此操作）

启动（如容器丢失需重建）：

```bash
docker run -d --name rsshub -p 1200:1200 -e TZ=Asia/Shanghai -e NODE_ENV=production diygod/rsshub:latest
```

健康检查与版本确认（**镜像更新频繁，升级前先看上游 release**）：

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:1200/        # 200 = 存活
docker inspect rsshub --format '{{.Config.Image}} {{.State.Status}}'
docker exec rsshub cat package.json | grep '"version"'               # 当前版本
docker logs rsshub --tail 50 2>&1 | grep -i error                    # 路由级报错定位
```

升级注意：`docker pull diygod/rsshub:latest` 后重建容器；**路由行为可能随版本变化**，
升级后必须重测在用路由（见第三节清单），失败路由读容器日志定位（反爬/上游改版/路由名变更三类）。

排错第一入口是容器日志，错误分三类，处置方式不同：
1. **上游反爬/改版**（如 `anti-bot challenge`、上游 API 404）→ 本地无解，换源或等上游
2. **中转服务死亡**（如微信路由的 careerengine.us 404）→ 换替代路由/自建中转
3. **路由名不存在**（404）→ 查官方文档 `docs.rsshub.app` 用对路由名

### ⚠️ 全部路由突然 502 = Docker Desktop 没在跑（macOS 高频坑）

RSSHub 容器跑在 Docker Desktop 里；**Desktop 退出/重启期间，1200 端口只剩转发层，所有路由（含首页）返回 502**。
容器重启策略为 `unless-stopped`，Docker daemon 恢复后自动拉起，无需手动干预。

诊断顺序（2026-10-25 凌晨实测：Docker 退出 → 全源 502 → 启动 Docker 后容器自愈）：

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:1200/   # 首页也 502 → 不是路由问题
docker info >/dev/null 2>&1 || open -a Docker                   # daemon 不在就启动 Desktop
# 等待就绪后 docker inspect rsshub --format '{{.State.Status}}' 确认 running，再复测路由
```

## 三、已核实渠道清单（2026-10-24 实测）

### ✅ 可用（已全部登记进 sources.json）

| 路由 | 内容 | 实测时效 | 登记状态 |
|---|---|---|---|
| `rsshub://36kr/newsflashes` | 36氪快讯 | ~60 分钟（缓存边界） | **enabled**（生产在用） |
| `rsshub://cls/telegraph` | 财联社电报 | **2 分钟** | 备用，推荐首选启用 |
| `rsshub://wallstreetcn/live/global` | 华尔街见闻·全球直播 | 1 分钟，100 条 | 备用（与财联社二选一） |
| `rsshub://wallstreetcn/news` | 华尔街见闻·新闻 | 6 分钟 | 备用（优先 live 版） |
| `rsshub://gelonghui/live` | 格隆汇·直播 | 0 分钟 | 备用（与财联社重叠最高） |
| `rsshub://huxiu/article` | 虎嗅·文章 | 5 分钟 | 备用 |
| `rsshub://caixin/latest` | 财新·最新 | 31 分钟 | 备用（AI/出海密度低） |
| `rsshub://sspai/matrix` | 少数派·矩阵 | 低频（可达 49h） | 备用（画像弱） |

**重叠警告**：财联社 / 华尔街见闻直播 / 格隆汇直播三家快讯内容高度重叠，
**同开只增加去重负担不增加信息量**，启用其一即可。

### ❌ 已核实不可用（勿再尝试）

| 路由 | 原因（2026-10-24 核实） |
|---|---|
| `36kr/information/web_news`（36氪文章） | 36氪网页反爬挑战（`anti-bot challenge`），快讯接口能过、文章接口过不了 |
| `wechat/uread`、`wechat/ce`、`wechat/wewea` 等 | 免费中转 `careerengine.us` 已 404（服务死亡） |
| `pedaily/news`、`zhidx/topic`、`huodongxing/search/:kw` | 路由名不存在（404/400），需查官方文档确认现名 |
| `geekpark/top`、`geekpark/breakingnews` | 极客公园上游 API 端点 404（上游改版） |
| `latepost/article` | 503（付费墙倾向） |

## 四、微信公众号路由专题（为什么难、怎么恢复）

微信公众号**没有官方公开 API**，RSSHub 的微信路由都是借第三方通道：

1. **`wechat/ce` / `wechat/uread`（默认路）**：走免费中转 careerengine.us → **已死**，等上游复活或弃用
2. **`wechat/wechat2rss/:id`**：走 [wechat2rss.xlab.app](https://wechat2rss.xlab.app) 公共服务，
   免费、无需配置，但公众号需被社区收录（实测 IT桔子未收录）。冷门号大概率没有
3. **自部署 wechat2rss**：需安卓环境抓包获取微信会话凭证，门槛高
4. **自部署 wewe-rss**：Docker 部署后用一个微信号**扫码登录**，借微信读书接口读公众号。
   所谓"token"即扫码后生成的会话凭证——**不是从腾讯申请**，有效期内有效、过期需重扫，
   且有微信号风控风险。**不到必要不建议折腾**（IT桔子官网 + 财联社已能覆盖融资信息大半）

## 五、启用一个备用源的标准动作

1. 改工作区 `~/.market-intel/sources.json`：对应源 `"enabled": true`
2. 试跑一轮采集确认健康：`pipeline.py --run`（看该源 ok/failed 与条目数）
3. 新源的窗口从首个快照起算（`windowed: true`），首轮只会增量入库，不会误报历史
