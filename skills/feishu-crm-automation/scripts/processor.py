import time
from datetime import datetime
import sys
import os

# 路径保护
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from actions import search_user, search_product, search_company  # noqa: E402


def date_to_timestamp(date_str):
    """将 YYYY/MM/DD 格式转换为 13 位毫秒时间戳"""
    # 已经是时间戳了
    if isinstance(date_str, int):
        return date_str
    try:
        dt = datetime.strptime(date_str.strip(), "%Y/%m/%d")
        return int(time.mktime(dt.timetuple()) * 1000)
    except Exception:
        # 如果解析失败，返回当前时间
        return int(time.time() * 1000)


def enrich_record(raw_data):
    """
    将 AI 提取的原始数据转换为 aPaaS 记录格式
    raw_data 示例: {
        "date": "2026/03/05",
        "customer": "客户 A",
        "product": "某个产品",
        "assignee": "曹培亚",
        "content": "跟进记录测试",
        "next_plan": "下一步 xxxx"
    }
    """
    enriched = {}

    # 1. 标题与内容描述 (text_describe)
    # 逻辑：如果 raw_data 中没有明确提供，则使用自动生成的默认格式
    if raw_data.get("text_title"):
        enriched["text_title"] = raw_data["text_title"]
    else:
        customer_name = raw_data.get("customer", "未知客户")
        enriched["text_title"] = f"{customer_name} 跟进记录"

    if raw_data.get("text_describe"):
        enriched["text_describe"] = raw_data["text_describe"]
    else:
        content = raw_data.get("content", "")
        next_plan = raw_data.get("next_plan", "")
        enriched["text_describe"] = (
            f"【跟进内容】\n{content}\n\n【下一步计划】\n{next_plan}"
        )

    # 2. 时间相关字段
    # 报告时间 (datetime_report - 必填)
    report_time = date_to_timestamp(raw_data.get("date", ""))
    enriched["datetime_report"] = report_time

    # 工单发起时间 (datetime_proposed)
    # 逻辑：不填写的情况下默认用 客户报告时间 + 10 分钟 (600,000 毫秒)
    if raw_data.get("proposed_date"):
        enriched["datetime_proposed"] = date_to_timestamp(raw_data["proposed_date"])
    else:
        enriched["datetime_proposed"] = report_time + (10 * 60 * 1000)

    # 3. 处理人映射 (lookup_handler - 关联用户对象)
    if raw_data.get("assignee"):
        user_res = search_user(raw_data["assignee"])
        u_items = user_res.get("data", {}).get("items", [])
        if u_items:
            enriched["lookup_handler"] = {"_id": u_items[0]["_id"]}

    # 4. 客户公司映射 (lookup_company - 必填)
    if raw_data.get("customer"):
        comp_res = search_company(raw_data["customer"])
        c_items = comp_res.get("data", {}).get("items", [])
        if c_items:
            enriched["lookup_company"] = {"_id": c_items[0]["_id"]}

    # 5. 产品映射 (lookup_product - 必填)
    # 策略：如果 raw_data 里没有明确的 product，但 AI 嗅探到了 keywords，则尝试依次查询
    product_query = raw_data.get("product") or raw_data.get("guessed_keywords")

    if product_query:
        # 如果是列表则取第一个，如果是字符串则直接查
        query_term = (
            product_query[0] if isinstance(product_query, list) else product_query
        )
        prod_res = search_product(query_term)
        p_items = prod_res.get("data", {}).get("items", [])
        if p_items:
            enriched["lookup_product"] = {"_id": p_items[0]["_id"]}

    # 6. 选项字段必填项默认值 (根据 API 文档定义)
    # 逻辑：如果 raw_data 中没有明确指定，则使用默认值
    enriched["option_level"] = raw_data.get("option_level", "option_minor")  # 默认 P3
    enriched["option_type"] = raw_data.get(
        "option_type", "option_cosultation"
    )  # 默认问题处理

    return enriched
