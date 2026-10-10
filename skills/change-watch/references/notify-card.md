# 通知卡片模板（飞书 Interactive Card 2.0）

> 三个目标：**清晰**（一眼看出在监控什么、有几条、哪条要紧）、**精炼**（每条三行以内，最多 5 块）、**可溯源**（每条带来源链接）。
>
> **硬规则：卡片是给读者看的，不是给工程师看的。** 卡片里**不允许**出现脚本名、文件路径、机器枚举值（`normal` / `added` / `rss` 之类），一律写成人话。

## 一、先把机器值翻成人话（必须照做）

| 机器值 | 卡片上必须显示 | 颜色 |
|---|---|---|
| `significance: high` | **重要** | `red` |
| `significance: normal` | **常规** | `blue` |
| `significance: low` | **次要** | `neutral` |
| `significance: unknown` | **不上卡**（脚本强制 `notify: false`：没判断过的东西不许发出去） | — |
| `change_kind: removed` | **已下架** | `red` |
| `change_kind: changed` | **有变化** | `yellow` |
| `change_kind: added` | 不显示标签（增量通知里"新增"是默认语义） | — |

**其余一律不得原样上卡：**

| ❌ 不要出现 | ✅ 应该写成 |
|---|---|
| `chrome-blog`、`x-google` | 用该源的 `label`：「Chrome 开发者博客」「X @Google」 |
| `normal` / `low` / `high` | 中文重要性标签（见上表） |
| `added` / `removed` / `changed` | 中文变化标签（见上表） |
| `rss` / `json_ld_posts` / `x_timeline` | 不出现（这是实现细节） |
| `~/.change-watch`、`query.py`、`out/latest.json` | 不出现。检索入口在会话里说，不写进卡片 |
| `change-watch`（技能名） | 用目标名，例如「Google 变更监控简报」 |

## 二、标题必须写清"在监控什么"

- ✅ `Google 变更监控简报` —— 目标名 + 用途，读者一眼知道这条通知是关于什么的
- ❌ `change-watch 变更简报` —— 技能名对读者没有信息量

目标名取配置里该 target 的 `name`（没有就取 `id`）。同时监控多个目标时，**按目标分别发卡**（每张卡只讲一个目标），不要让标题失焦。
副标题放时间与扫描范围：`2026-10-09 23:10 · 扫描 18 个来源`。

## 三、字段映射

| 条目字段 | 卡片槽位 |
|---|---|
| `change_kind` | 块首行**变化标签**（仅 `removed` / `changed` 时显示，见 §一） |
| `significance` | 块首行**重要性标签** |
| `type` | 块首行**类型标签**（`neutral` 色） |
| `title` | 块首行**粗体标题** |
| `summary` | 块第二行：一句话说清"变了什么、对读者意味着什么" |
| `url` + 同事件其他来源 | 块第三行灰色小字「来源 N 个 · [名称](链接)」 |

排序：重要 → 常规 → 次要；同一事件多来源**合并为一块**并列出全部链接。块数 > 5 时只放重要/常规，其余收进末尾一行「另有 N 条次要变更」。

## 四、有变更（骨架）

```json
{
  "schema": "2.0",
  "config": { "update_multi": true, "width_mode": "default",
    "style": { "text_size": {
      "title":   { "default": "heading-2", "pc": "heading-2", "mobile": "heading-3" },
      "body":    { "default": "normal",    "pc": "normal",    "mobile": "normal" },
      "caption": { "default": "notation",  "pc": "notation",  "mobile": "notation" } } } },
  "header": {
    "title":    { "tag": "plain_text", "content": "<目标名> 变更监控简报" },
    "subtitle": { "tag": "plain_text", "content": "<时间> · 扫描 <N> 个来源" },
    "template": "blue",
    "icon": { "tag": "standard_icon", "token": "bell_outlined", "color": "blue" },
    "text_tag_list": [
      { "tag": "text_tag", "text": { "tag": "plain_text", "content": "<N> 条变更" }, "color": "blue" }
    ]
  },
  "body": { "direction": "vertical", "padding": "12px 12px 20px 12px", "elements": [
    { "tag": "interactive_container", "width": "fill", "has_border": true,
      "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
      "padding": "12px 12px 12px 12px", "vertical_spacing": "4px", "margin": "0px 0px 12px 0px",
      "elements": [
        { "tag": "markdown", "content": "<text_tag color='<变化色>'><变化标签></text_tag> <text_tag color='<重要性色>'><重要性></text_tag> <text_tag color='neutral'><类型></text_tag> **<标题>**" },
        { "tag": "markdown", "content": "<一句话说明>" },
        { "tag": "markdown", "content": "<font color='grey'>来源 N 个 · [名称](URL)</font>", "text_size": "notation" }
      ] },
    { "tag": "markdown", "content": "<font color='grey'>扫描 <N> 个来源 · 失败 <N> · 停更 <N>（<中文源名>）</font>", "text_size": "notation" },
    { "tag": "markdown", "content": "<font color='grey'>明细已归档，可按时间线 / 类型 / 对象检索。</font>", "text_size": "notation" }
  ] }
}
```

- 变更块 = 上面 `interactive_container` 重复 N 次，**最后一个块不写 `margin`**。
- `change_kind` 为 `added` 时，块首行**去掉变化标签**（只剩重要性 + 类型 + 标题）。
- 出现重要级别变更（模型下架、重大政策）时，header 的 `text_tag_list` 再加一个 `{ "color": "red", "content": "含重要变更" }`。

## 五、无变更（骨架）

同 header，`text_tag` 改为 `{"content": "无变更", "color": "green"}`，body 只留：

```json
[
  { "tag": "interactive_container", "width": "fill", "has_border": true,
    "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
    "padding": "12px 12px 12px 12px", "vertical_spacing": "4px", "margin": "0px 0px 12px 0px",
    "elements": [
      { "tag": "markdown", "content": "**本轮无变更**" },
      { "tag": "markdown", "content": "全部 <N> 个来源与上次快照一致，无新增、下架或状态变化。" }
    ] },
  { "tag": "markdown", "content": "<font color='grey'>扫描 <N> 个来源 · 失败 <N> · 停更 <N>（<中文源名>）</font>", "text_size": "notation" }
]
```

> 按默认策略 **无变更时不发卡**（不打扰）；此变体仅用于用户主动询问、或扫描有明显异常（失败 / 停更多起来）需要知会的场合。

扫描异常（停更 / 抓取失败）若需上卡，追加一个 `yellow-50` 描边块，**每类各占一个独立 markdown 元素**：

```json
{ "tag": "interactive_container", "width": "fill", "has_border": true,
  "border_color": "yellow-100", "corner_radius": "8px", "background_style": "yellow-50",
  "padding": "12px 12px 12px 12px", "vertical_spacing": "8px",
  "elements": [
    { "tag": "markdown", "content": "**停更源（N）**（已成功抓取 · 对方长期未更新）：\n- [中文源名](主页)（约N天）" },
    { "tag": "markdown", "content": "**抓取失败（N）**（本次无法连接该源 · 非停更）：\n- [中文源名](主页)" }
  ] }
```

> **必须拆成多个 markdown 元素，不能用一个元素塞两节。** 飞书 markdown 会把「`- 列表项`」紧跟的下一行当作该列表项的**续行**，若两节共处一个元素，「抓取失败」会被并进「停更源」最后一条的圆点里，视觉上混成一团。两类语义必须写死区分：**停更 = 已成功抓取但对方长期未更新；抓取失败 = 本次连不上（非停更）**。

## 六、发送前的自检（逐条过，过不了就别发）

1. 标题是否说明了**在监控什么**（目标名），而不是技能名？
2. 扫一眼：还有没有 `normal` / `low` / `abc-blog` 这类机器值、脚本名、文件路径？
3. 每条变更是否都有**来源链接**？同事件的多个来源是否已合并成一块？
4. 末尾统计行是否含：扫描来源数、失败数、停更数，并且**停更源用中文名点名**？停更 / 抓取失败的来源在卡片里应渲染成**可点击链接**（由 feed 地址推导出的主页），并附「约N天」时长，方便直接跳转溯源。
5. 无变更时是否真的不该发？（默认不发）
6. 测试性质的卡片是否已在 header 标了「链路测试」（`yellow`）？

## 七、发送方式与降级链（依次尝试）

1. **宿主 AI 工具的飞书连接器**：用宿主提供的发消息能力发送 `interactive` 卡片。以**用户身份**发消息通常缺 `im:message.send_as_user` 权限，优先用 **bot 身份**。
2. **本地 lark-cli**（连接器的 CLI，WorkBuddy 环境已内置）：
   ```bash
   # 私信（默认）
   lark-cli im +messages-send --as bot --user-id <用户openId> \
     --msg-type interactive --content '<卡片JSON>'
   # 群聊强提醒：把 --user-id 换成 --chat-id（二者互斥）
   lark-cli im +messages-send --as bot --chat-id <群chat_id> \
     --msg-type interactive --content '<卡片JSON>'
   ```
   openId 失效用 `lark-cli auth status` 重查。JSON 较长时**写入文件后用 `--content - < 文件路径` 发送**（不要用 `--content @文件路径`：`@` 形式受路径白名单限制，仅 cwd / /tmp / ~/files 可用，工作区卡片默认不在其中，会被拒绝）。
   **群聊强提醒（高优变更额外 @ 群）**：`targets.json` 顶层 `notify` 块配 `chat_id` + `mention`；
   某目标含 high 变更时 `log_change.py` 额外产出 `card-<目标>-<时间>-group.json`（红头 + 顶部
   「⚠️ 重要变更，请关注 @」横幅），用上面的 `--chat-id` 命令发到群。状态简报若本轮有抓取失败的源，
   同样额外产出 `*-group.json` 并发群 @。`mention: "all"` = `@所有人`（**需群主在群设置开启「@所有人」权限**，
   否则发送会失败，此时把 `mention` 改成 `["ou_具体openId"]` 只 @ 指定人即可）。群 `chat_id` 用
   `lark-cli im +chat-search --query 群名` 查。
   **改进已发出的卡**（同一张卡原地更新，不刷屏）：`lark-cli im messages patch --as bot --message-id <om_…> --data '{"content":"<卡片JSON字符串>"}'`（卡片须为本应用所发、14 天内）。
   - **扫描状态简报（无变更 / 有异常时知会）**：`python3 scripts/log_change.py --status-card` 生成 `out/card-status-<时间>.json`（notify-card.md §五 变体，从 `out/latest.json` 取数据），按上面命令发送即可，无需手拼。
3. **都失败**：把简报全文写入工作区 `out/notify-unsent-<时间>.md`（含失败原因与时间），并在会话内**明确提醒用户**「飞书发送失败，简报已落盘在 <路径>」。不允许静默吞掉。

## 八、配色与样式基准

- 已按飞书卡片样式标准（P0–P7）验证过的组合：header `blue` + 描边块 `blue-50`/`blue-100` + 灰色 `notation` 小字统计。
- 语义固定：**红 = 要紧（重要 / 已下架）、蓝 = 常规信息、绿 = 一切正常、灰 = 次要、黄 = 有变化 / 测试**。改配色时保持这套语义。
