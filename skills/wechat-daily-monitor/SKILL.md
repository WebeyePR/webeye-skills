---
name: wechat-daily-monitor
description: |
  微信公众号监控与历史文章获取工具。以本地 skill 目录下的 watchlist.txt 为唯一真源，管理所有公众号的增/删/查操作。支持两大功能：
  1. 每日监控：基于 watchlist.txt 监控指定公众号列表的当日发文情况，生成每日摘要简报。
  2. 历史文章：获取公众号历史发文列表，支持翻页、指定页码、费用控制。
  当用户需要：(1) 监控竞品账号每日更新，(2) 追踪关注领域头部账号动态，(3) 获取公众号历史文章列表，(4) 批量查看公众号发文情况，(5) 生成公众号更新日报时使用此技能。
---

# 微信公众号监控与历史文章

两大功能：每日发文监控 + 历史文章获取。

## 工作流程

### 功能A：每日发文监控

#### 1. 提取公众号列表

该技能以本地 skill 目录下的 `watchlist.txt` 为**唯一真源**。所有涉及公众号的增/删/查操作，均直接读取或修改此文件。

从用户输入中提取需要监控的公众号名称：

**支持格式：**
- 修改本地监控名单：用户要求新增/删除监控时，直接通过 `echo` 或文本编辑修改与本 SKILL.md 同目录下的 `watchlist.txt`。
- 直接查询列表：查看 `watchlist.txt` 文件内容以确认当前监控名单。
- 执行监控：始终从 `watchlist.txt` 读取，不接受临时传入的账号列表作为实际执行输入。

**公众号标识：** 仅支持公众号名称（如"机器之心"、"通往AGI之路"），不支持 biz 或 URL。

#### 2. 获取当日发文数据

使用监控脚本获取每个公众号的当天发文情况：

```bash
# 默认从 watchlist.txt 读取全部监控账号
python3 scripts/fetch_daily_posts.py \
  -o /tmp/wechat_daily_monitor.json
```

**参数说明：**
- 账号输入统一来自 `watchlist.txt`，文件中每行一个公众号名称
- `-o`: 保存到文件
- `--key`: API 密钥（可选，默认使用环境变量）
- 费用：0.08元/次（按公众号名称查询）

#### 3. 生成每日摘要报告

根据获取的数据，生成简洁的每日摘要简报：

**报告包含三部分：** 更新概览表格（公众号/状态/文章数/时间）→ 今日发文详情（标题/时间/链接）→ 统计摘要（总数/最早最晚发文）。

完整报告模板和字段说明见 [references/report_template.md](references/report_template.md)。

#### 4. 保存报告

**命名规则**：`每日监控_YYYYMMDD.md`

**存储路径**：保存到以日期命名的文件夹

```bash
# 创建日期文件夹
mkdir -p $(date +%Y%m%d)

# 保存报告
# 完整路径：{日期文件夹}/每日监控_YYYYMMDD.md
```

完成后告知用户：
> 监控报告已保存至：`20260330/每日监控_20260330.md`

---

### 功能B：获取公众号历史文章

#### 使用场景

当用户需要：
- 获取某个公众号的全部历史发文列表
- 分析公众号历史发布规律
- 导出公众号历史文章数据

#### 调用方式

```bash
# 默认获取 watchlist 中每个账号最近 1 页（5次发文，约5~40篇文章）
python3 scripts/fetch_history_posts.py \
  -o /tmp/history.json

# 只获取 watchlist 中每个账号前 N 页（控制费用）
python3 scripts/fetch_history_posts.py \
  --max-pages 5 \
  -o /tmp/history.json

# 获取 watchlist 中每个账号的指定页码
python3 scripts/fetch_history_posts.py \
  --page 3 \
  -o /tmp/history.json
```

#### 费用说明

0.08元/页（每页5次发文，每次1~8篇文章）。默认只获取第1页。需要更多请用 `--max-pages` 指定页数，`--max-pages 0` 获取全部（费用较高，谨慎使用）。QPS 限制 5 次/秒，脚本已内置 1.2 秒翻页间隔。

---

## API 配置

API 详细参数见 [references/api_reference.md](references/api_reference.md)。

## 报告模板

完整报告模板见 [references/report_template.md](references/report_template.md)。

## 配置说明

### 环境变量

**方式1：环境变量**
```bash
export JIZHILE_API_KEY="your_api_key_here"
```

**方式2：.env 文件**（推荐）
```bash
# 在 skill 目录下创建 .env 文件
echo 'JIZHILE_API_KEY="your_api_key_here"' > {SKILL_DIR}/.env
```

### 监控列表配置（唯一真源）

本地技能目录下的 `watchlist.txt` 是监控名单的唯一真源，所有长期监控的账号均以该文件为准。

**查看监控列表**：
```bash
cat {SKILL_DIR}/watchlist.txt
```

**新增监控**：
```bash
echo "新公众号名称" >> {SKILL_DIR}/watchlist.txt
```

**删除监控**：
请使用适当的文本编辑工具（如 `sed` 或 `grep -v`）从文件中移除对应的公众号名称。

使用时：
```bash
python3 scripts/fetch_daily_posts.py
python3 scripts/fetch_history_posts.py --max-pages 1
```
