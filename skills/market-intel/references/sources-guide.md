# 公共源登记册字段说明（sources.json）

两个需求（`intel` 行业情报 / `lead` 线索发现）**共用一份登记册**，按 `serves` 按需选用；
采集参数全源统一，不按需求分裂。改配置不改代码。

## 顶层

| 字段 | 说明 |
|---|---|
| `version` | 配置版本，结构变更时递增 |
| `settings` | 全局采集参数（沿用 change-watch 语义）：`stale_after_hours` 停更阈值、`request_timeout_seconds`、`request_retries`、`collect_concurrency` 全局并发、`host_concurrency` 单域并发、`collect_wall_clock_seconds` 整轮壁钟上限 |
| `sources` | 源数组 |

## 源字段

| 字段 | 必填 | 说明 | 沿用来源 |
|---|---|---|---|
| `id` | 是 | 全局唯一标识，同时是快照文件名 | change-watch |
| `name` | 是 | 中文显示名（卡片、停更告警用它点名） | change-watch `label` |
| `kind` | 是 | 源类型：`rss` / `mp_account` / `web_page` | change-watch + AIHOT `mp_account` |
| `url` | 是 | 采集地址 | — |
| `enabled` | 否 | 默认 true；false 跳过采集（预留源用） | AIHOT 暂停语义 |
| `serves` | 是 | `["intel"]` / `["lead"]` / 两者——**本技能新增**，标记服务哪些需求 | — |
| `tier` | 否 | 信源分级 `T1` 官方一手 / `T1_5` 准官方 / `T2` 媒体个人（AIHOT 语义） | AIHOT |
| `owner_entity` | 否 | 归属企业 id（web_page 招聘监测必填，档案归到它名下） | AIHOT `owner_entity_id` |
| `tags` | 否 | 主题标签（中文数组，自由扩展） | AIHOT |
| `windowed` | 否 | 有限窗口流声明；true 时窗口滚动不报消失 | change-watch |
| `stale_after_hours` | 否 | 覆盖全局停更阈值；null 不检查 | change-watch |
| `timeout_seconds` | 否 | 单源超时覆盖 | change-watch |
| `config` | 否 | 源类型私有参数（web_page 的 `keywords`） | — |
| `notes` | 否 | 备注：实测结论、 why 这个源（写清，方便后人接手） | — |

## 选源纪律

1. **新源先实测再入库**：中文媒体原生 RSS 大多已失效，登记册里只放实测可用的地址，
   未验证的写进 `notes` 并 `enabled: false`。
2. **公众号必须低频 + windowed**，且不做唯一信源。
3. **web_page 的 keywords 决定信噪比**：招聘页选"岗位名/职能词"，不要选泛词。
4. 加源后先 `--baseline` 再恢复 `--run`，避免历史当新消息。
