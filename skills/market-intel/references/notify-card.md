# 通知卡片与发送（飞书）

骨架、翻译表、降级链、配色语义**全部沿用 change-watch 的 notify-card.md**（同仓库同纪律），
本文件只写 market-intel 的差异部分。通用规范（Interactive Card 2.0 骨架、机器值翻译表、
"卡片不要手拼"、无变更不发卡）直接参照
`../change-watch/references/notify-card.md`，此处不重复。

## 一、market-intel 的卡片族

| 卡片（脚本产出） | 内容 | header template | 接收方 |
|---|---|---|---|
| `card-daily-<时间>.json` | 每日内参（需求文档三段式，indigo 头图） | indigo | 群 |
| `card-intel-<时间>.json` | 本轮情报简报（含类型/重要性标签） | blue | 群 |
| `card-intel-<时间>-group.json` | 含重要动态时的群强提醒（首行 @ 提醒） | red | 群 @ |
| `card-leads-<时间>.json` | 新线索（含匹配度、命中规则、证据） | green/red | 本人私信 |
| `card-leads-<时间>-group.json` | P0 线索群卡（首行 @ 提醒） | red | 群 @ |
| `card-digest-<时间>.json` | 内参卡渲染失败时的 simple 降级版 | blue | 群 |
| `card-status-<时间>.json` | 扫描状态知会（失败/停更点名） | yellow/green | 本人私信 |

群卡首行 @ 提醒由 notify.json `notify.mention` 驱动（`"all"` = `<at id=all></at>`，
`["ou_xxx"]` = 指定人，空 = 不 @）。

## 二、线索卡的翻译纪律（在 change-watch 翻译表之上追加）

| ❌ 不要出现 | ✅ 应该写成 |
|---|---|
| `matched_groups: ["funded-and-hiring"]` | 命中规则的中文名：「近期融资且在招」 |
| `score: 72` | 「匹配度 72」 |
| `entity_id: "moonshot"` | 档案中文名：「月之暗面」 |
| `unknown:xxx` | 「未识别企业（待归并）」，且**原则上不该上卡**——判断阶段尽量给出可归一公司名 |
| `funding_events` / `hiring_signals` | 「近期融资」「在招岗位」等人话 |

## 三、发送命令（lark-cli，连接器不可用时的兜底）

```bash
# 私信（线索卡、状态卡）—— bot 身份
lark-cli im +messages-send --as bot --user-id <用户openId> \
  --msg-type interactive --content - < out/card-leads-XXXX.json

# 群（情报简报、日报、内参、P0 线索群卡）—— 一律 user 身份
# 实测纪律（change-watch 沿用）：bot 发群卡 @所有人不生效且易被折叠，必须 --as user
lark-cli im +messages-send --as user --chat-id <群chat_id> \
  --msg-type interactive --content - < out/card-intel-XXXX.json
```

- openId 失效用 `lark-cli auth status` 重查；群 chat_id 用 `lark-cli im +chat-search --query 群名` 查。
- JSON 较长时**写入文件后用 `--content - < 文件路径`**（`@文件路径` 受路径白名单限制会拒绝）。
- 首选宿主 AI 工具的飞书连接器：私信走 bot 身份；群卡连接器不可用或 @ 失效时退 lark-cli `--as user`。
- 都失败：简报落 `out/notify-unsent-<时间>.md` 并在会话内**明确提醒用户**，不许静默吞掉。

## 四、发送纪律（沿用 change-watch"三刀"）

1. **不重复发**：线索按 dedupe_key（企业+命中组）7 天内只推一次；情报按 change_id 幂等。
2. **空跑别发**：本轮 0 条可通知内容 → 完全静默（每日摘要除外——间隔够长，有无结果都发）。
3. **噪声别发**：`unknown` 情报与未识别企业一律不上卡。
4. 测试卡必须在 header 标「链路测试」（yellow）。
