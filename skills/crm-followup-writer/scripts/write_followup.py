#!/usr/bin/env python3
"""
write_followup.py
=================
业务一部CRM跟进记录一键写入工具。

把"解析参数 + 查客户 record_id + 查人员 open_id + 组装 JSON + 调 lark-cli +
回报 record_id"打包成一个命令。

使用：
    python3 scripts/write_followup.py \
        --customer "CATL" \
        --date "2026-06-26" \
        --content "供应商入库完成..." \
        --summary "供应商入库完成" \
        --next-action "7 月底对客户 AI 做 workshop" \
        --next-plan "7 月底对客户 AI 做 workshop" \
        --contact "张继哲" \
        --internals "张三,李四" \
        --owners "王五"

依赖：
    - lark-cli（已安装并在 PATH）
    - Python 3.8+
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

# === 常量 ===
BASE_TOKEN = "XFl3b5yYvag1dTsksuKcrHbZnYc"
TABLE_ID = "tblGIUssGOxAZ1zM"           # 客户跟进记录表
CUSTOMER_TABLE_ID = "tblF6xyVs09BdGtA"  # 客户管理表
CUSTOMER_NAME_FIELD = "flduEYDucF"      # 客户管理表"客户简称"
VIEW_ID = "vew1ZNEles"

BASE_URL = f"https://webeye.feishu.cn/base/{BASE_TOKEN}"


# === 工具函数 ===

def run(cmd: list[str]) -> dict:
    """调 lark-cli 子命令，解析 JSON 返回。"""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"[ERROR] lark-cli 调用失败：{' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"[ERROR] 解析 lark-cli 返回失败：{e}", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        sys.exit(1)


def search_customer(customer_name: str) -> str:
    """在客户管理表查客户简称，返回唯一 record_id。"""
    data = run([
        "lark-cli", "base", "+record-search",
        "--as", "user",
        "--base-token", BASE_TOKEN,
        "--table-id", CUSTOMER_TABLE_ID,
        "--field", CUSTOMER_NAME_FIELD,
        "--value", customer_name,
        "--format", "json",
    ])

    if not data.get("ok"):
        print(f"[ERROR] 客户查询失败：{data}", file=sys.stderr)
        sys.exit(1)

    records = (data.get("data") or {}).get("records") or []
    if not records:
        print(f"[ERROR] 客户管理表里没找到：{customer_name}", file=sys.stderr)
        sys.exit(1)
    if len(records) > 1:
        print(f"[ERROR] 客户「{customer_name}」命中 {len(records)} 条，请精确匹配：")
        for r in records:
            print(f"  - {r.get('id')} : {r.get('fields', {}).get(CUSTOMER_NAME_FIELD)}")
        sys.exit(1)
    return records[0]["id"]


def search_user(name: str) -> str | None:
    """查人员 open_id。找不到返回 None。"""
    data = run([
        "lark-cli", "contact", "+search-user",
        "--as", "user",
        name,
        "--format", "json",
    ])
    if not data.get("ok"):
        return None
    users = (data.get("data") or {}).get("users") or []
    if not users:
        return None
    # 取第一个匹配
    return users[0].get("open_id")


def parse_names(names_str: str | None) -> list[dict]:
    """把 '张三,李四' 解析成 [{"id": "ou_xxx"}] 数组。"""
    if not names_str:
        return []
    out = []
    for name in [n.strip() for n in names_str.split(",") if n.strip()]:
        oid = search_user(name)
        if oid:
            out.append({"id": oid})
        else:
            print(f"[WARN] 没找到 {name} 的 open_id，已跳过", file=sys.stderr)
    return out


def parse_date(date_str: str) -> int:
    """把 '2026-06-26' 或 '2026/06/26' 解析为毫秒时间戳。"""
    s = date_str.replace("/", "-")
    dt = datetime.strptime(s, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, tzinfo=timezone(timedelta(hours=8))
    )
    return int(dt.timestamp() * 1000)


def parse_datetime(dt_str: str) -> int:
    """把 '2026-06-28 18:00' 解析为毫秒时间戳。"""
    s = dt_str.replace("/", "-")
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    return int(dt.timestamp() * 1000)


# === 主流程 ===

def main():
    p = argparse.ArgumentParser(description="业务一部CRM跟进记录一键写入")
    p.add_argument("--customer", required=True, help="客户简称")
    p.add_argument("--date", required=True, help="跟进日期 yyyy-MM-dd")
    p.add_argument("--content", required=True, help="跟进记录（详细）")
    p.add_argument("--summary", required=True, help="跟进总结（一句话）")
    p.add_argument("--next-action", required=True, help="下一步(AI用)")
    p.add_argument("--next-plan", help="下一步跟进计划（人类可读）")
    p.add_argument("--contact", help="沟通对象")
    p.add_argument("--internals", help="内部参与人（逗号分隔）")
    p.add_argument("--owners", help="任务执行者（逗号分隔）")
    p.add_argument("--status", default="未完成", choices=["未完成", "已完成"])
    p.add_argument("--deadline", help="任务截止时间 yyyy-MM-dd HH:mm")
    p.add_argument("--link", help="跟进记录链接（云文档/妙计）")
    p.add_argument("--no-sync", action="store_true", help="不勾选「同步任务」")
    args = p.parse_args()

    # 1) 查客户
    print(f"[1/4] 查询客户 {args.customer} ...", file=sys.stderr)
    customer_rid = search_customer(args.customer)
    print(f"       → record_id = {customer_rid}", file=sys.stderr)

    # 2) 查人员
    internals = parse_names(args.internals)
    owners = parse_names(args.owners)
    print(f"[2/4] 内部参与人 {len(internals)} 人，任务执行者 {len(owners)} 人", file=sys.stderr)

    # 3) 组装 fields
    fields = {
        "客户简称": [customer_rid],
        "跟进日期": parse_date(args.date),
        "跟进记录": args.content,
        "跟进总结": args.summary,
        "下一步(AI用)": args.next_action,
        "任务完成状态": args.status,
        "同步任务": not args.no_sync,
    }
    if args.next_plan:
        fields["下一步跟进计划"] = args.next_plan
    if args.contact:
        fields["沟通对象"] = args.contact
    if internals:
        fields["内部参与人"] = internals
    if owners:
        fields["任务执行者"] = owners
    if args.deadline:
        fields["任务截止时间"] = parse_datetime(args.deadline)
    if args.link:
        fields["跟进记录链接(云文档/妙计)"] = args.link

    # 4) 调 lark-cli 写入
    print(f"[3/4] 写入跟进记录 ...", file=sys.stderr)
    payload = json.dumps({"records": [{"fields": fields}]}, ensure_ascii=False)
    data = run([
        "lark-cli", "base", "+record-batch-create",
        "--as", "user",
        "--base-token", BASE_TOKEN,
        "--table-id", TABLE_ID,
        "--json", payload,
    ])

    if not data.get("ok"):
        print(f"[ERROR] 写入失败：{data}", file=sys.stderr)
        sys.exit(1)

    records = (data.get("data") or {}).get("records") or []
    if not records:
        print(f"[ERROR] 返回里没有 record：{data}", file=sys.stderr)
        sys.exit(1)
    rid = records[0].get("record_id") or records[0].get("id")
    url = f"{BASE_URL}?table={TABLE_ID}&view={VIEW_ID}&record={rid}"

    # 5) 回报
    print(f"[4/4] 完成 ✅\n", file=sys.stderr)
    print(json.dumps({
        "record_id": rid,
        "url": url,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
