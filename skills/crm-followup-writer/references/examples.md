# 写法样例

把 SKILL.md 里的命令组装成 3 个真实场景的样例，方便照抄。

---

## 样例 1：标准跟进同步

**用户口述**：
> 我的客户是 CATL，请帮我同步跟进记录。跟进记录：1. 确认供应商入库的进展，已完成系统平台入库与廉洁协议签署。2. 沟通跟进 Google AI 机会。CATL 从 IT 团队拆分出了 AIDC 团队，目前张继哲是该团队的对接人，同时负责业务前期采购对接。下一步：1. 需要对客户 AI 进行 workshop，预计在 7 月底 8 月初左右。2. 需要向客户确认 GCP 企业级服务目前香港线路都可以正常稳定使用 @侯瑞强

**组装命令**：

```bash
# 1) 查客户 record_id
lark-cli base +record-search --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblF6xyVs09BdGtA" \
  --field "flduEYDucF" \
  --value "CATL"
# → 假设返回 record_id = recm2Gv8vj

# 2) 查侯瑞强 open_id
lark-cli contact +search-user --as user "侯瑞强"
# → 假设返回 open_id = ou_875e8ca44dbaf678ffc36103c2df908b

# 3) 计算毫秒时间戳
python3 -c "import time; print(int(time.time()*1000))"
# → 假设返回 1750924800000

# 4) 写入
lark-cli base +record-batch-create --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblGIUssGOxAZ1zM" \
  --json '{
    "records": [
      {
        "fields": {
          "客户简称": ["recm2Gv8vj"],
          "跟进日期": 1750924800000,
          "跟进记录": "1. 确认供应商入库的进展，已完成系统平台入库与廉洁协议签署。\n2. 沟通跟进 Google AI 机会。CATL 从 IT 团队拆分出了 AIDC 团队，目前张继哲是该团队的对接人，同时负责业务前期采购对接。\n  a. 对 Claude 有需求，目前都在 AWS Bedrock 上，未来会考虑切分部分去 vertex\n  b. 对 Google AI 产品表示有兴趣，目前主要应用考虑在 Gemini 内的 google search 能力，deep research 能力。\n  c. 客户的考虑是香港专线是否可以稳定使用 Google 的 AI 服务\n  d. AIDC 为新成立部门，目前仍走 IT 预算。7 月底 8 月起 AIDC 会考虑自己开始预算决策\n  e. GCP 另有一些 Infra 机会可能在与 CATL 沟通，不确定。CATL 内部人员流动性大\n已与 Kenneth 同步",
          "跟进总结": "供应商入库完成；AIDC 团队对接 Google AI 机会，Gemini/Vertex 兴趣明确，7-8 月独立预算",
          "下一步(AI用)": "1. 7 月底 8 月初对客户 AI 进行 workshop，保持互动跟进\n2. 确认 GCP 企业级服务香港线路稳定可用（owner：侯瑞强）",
          "下一步跟进计划": "7 月底 8 月初对客户 AI 进行 workshop；持续跟进 AIDC 独立预算动向",
          "沟通对象": "张继哲（CATL AIDC 团队）",
          "任务执行者": [{"id": "ou_875e8ca44dbaf678ffc36103c2df908b"}],
          "任务完成状态": "未完成",
          "同步任务": true
        }
      }
    ]
  }'
```

**回报用户**：
```
✅ 已写入：record_id = recvXXXXX
[查看该记录](https://webeye.feishu.cn/base/XFl3b5yYvag1dTsksuKcrHbZnYc?table=tblGIUssGOxAZ1zM&view=vew1ZNEles&record=recvXXXXX)
```

---

## 样例 2：含截止时间的合同/任务跟进

**用户口述**：
> 美团跟进：合同这周内要签，@李航 帮忙 review 一下付款条款，deadline 2026/6/28 18:00

**组装命令**：

```bash
# 1) 查美团 record_id + 李航 open_id
lark-cli base +record-search --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblF6xyVs09BdGtA" \
  --field "flduEYDucF" \
  --value "美团"

lark-cli contact +search-user --as user "李航"

# 2) 计算 2026/6/28 18:00 毫秒时间戳
python3 -c "import time; print(int(time.mktime(time.strptime('2026-06-28 18:00:00', '%Y-%m-%d %H:%M:%S'))*1000))"
# → 1751110800000

# 3) 写入（注意任务截止时间、任务完成状态默认"未完成"）
lark-cli base +record-batch-create --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblGIUssGOxAZ1zM" \
  --json '{
    "records": [
      {
        "fields": {
          "客户简称": ["recm美团"],
          "跟进日期": <today_ms>,
          "跟进记录": "美团合同这周内要签，李航帮忙 review 付款条款，deadline 2026/6/28 18:00",
          "跟进总结": "美团合同本周内签订，李航协助 review 付款条款",
          "下一步(AI用)": "李航 review 付款条款，deadline 2026-06-28 18:00",
          "下一步跟进计划": "本周内完成合同签订",
          "沟通对象": "美团商务团队",
          "内部参与人": [{"id": "ou_李航"}],
          "任务执行者": [{"id": "ou_李航"}],
          "任务完成状态": "未完成",
          "任务截止时间": 1751110800000,
          "同步任务": true
        }
      }
    ]
  }'
```

---

## 样例 3：跟进妙记链接

**用户口述**：
> 比亚迪今天 workshop 的妙记是 https://example.feishu.cn/minutes/xxxxx ，核心结论是 Q3 启动 POC，预算 50 万

**组装命令**：

```bash
# 写入时把妙记链接放到「跟进记录链接(云文档/妙计)」
lark-cli base +record-batch-create --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblGIUssGOxAZ1zM" \
  --json '{
    "records": [
      {
        "fields": {
          "客户简称": ["recm比亚迪"],
          "跟进日期": <today_ms>,
          "跟进记录": "比亚迪今天 workshop，核心结论：Q3 启动 POC，预算 50 万",
          "跟进总结": "比亚迪 Q3 启动 POC，预算 50 万",
          "下一步(AI用)": "Q3 启动 POC，准备 POC 方案",
          "沟通对象": "比亚迪 IT 团队",
          "任务完成状态": "未完成",
          "跟进记录链接(云文档/妙计)": "https://example.feishu.cn/minutes/xxxxx",
          "同步任务": true
        }
      }
    ]
  }'
```

---

## 一键脚本调用

更简单的方式，直接调脚本：

```bash
python3 scripts/write_followup.py \
  --customer "CATL" \
  --date "2026-06-26" \
  --content "1. 供应商入库完成；2. AIDC 团队对接 Google AI..." \
  --summary "供应商入库完成；AIDC 团队对接 Google AI 机会" \
  --next-action "7 月底 8 月初对客户 AI 进行 workshop；@侯瑞强 确认 GCP 香港线路" \
  --next-plan "7 月底 8 月初对客户 AI 进行 workshop" \
  --contact "张继哲（CATL AIDC）" \
  --owners "侯瑞强"
```

脚本会自动：查客户 record_id → 查 open_id → 算时间戳 → 拼 JSON → 调 `lark-cli base +record-batch-create` → 回报 record_id。
