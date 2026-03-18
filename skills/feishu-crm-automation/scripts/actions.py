import sys
import os

# 路径保护：确保脚本所在目录在 sys.path 中
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from client import api_client  # noqa: E402
from config import NAMESPACE, OBJECT_PRODUCT, OBJECT_USER, OBJECT_TICKET, OBJECT_COMPANY  # noqa: E402


# 工单配置元数据：工单类型、工单级别
TICKET_OPTIONS = {
    "option_type": [
        {"label": "问题处理", "value": "option_cosultation"},
        {"label": "操作变更", "value": "option_defects"},
        {"label": "Billing问题", "value": "option_issues"},
        {"label": "技术方案或报价", "value": "option_complaint"},
        {"label": "其他事务", "value": "option_sugesstion"},
    ],
    "option_level": [
        {"label": "P3-低", "value": "option_minor"},
        {"label": "P2-中", "value": "option_major"},
        {"label": "P1-高", "value": "option_critical"},
        {"label": "P0-紧急/阻断", "value": "option_blocked"},
    ],
}


def build_filter(field_name, object_name, value, operator="equals"):
    """构造 aPaaS 标准筛选参数"""
    return {
        "conditions": [
            {
                "operator": operator,
                "left": {
                    "type": "metadataVariable",
                    "settings": f'{{"fieldPath":[{{"fieldApiName": "{field_name}","objectApiName": "{object_name}"}}]}}',
                },
                "right": {"type": "constant", "settings": f'{{"data":"{value}"}}'},
            }
        ],
        "expression": "1",
    }


def search_product(product_name, page_size=10, operator="contains"):
    """根据名称查询产品记录。支持 operator: contains, equals"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_PRODUCT}/records_query"
    payload = {
        "select": ["_id", "text_name", "option_status"],
        "filter": build_filter("text_name", OBJECT_PRODUCT, product_name, operator),
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def search_user(user_name, page_size=10, operator="contains"):
    """根据姓名模糊查询用户 ID。支持 operator: contains, equals"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_USER}/records_query"
    payload = {
        "select": ["_id", "_name", "_email"],
        "filter": build_filter("_name", OBJECT_USER, user_name, operator),
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def search_user_by_email(email, page_size=1, operator="equals"):
    """根据邮箱精确查询用户 ID"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_USER}/records_query"
    payload = {
        "select": ["_id", "_name", "_email"],
        "filter": build_filter("_email", OBJECT_USER, email, operator),
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def search_company(company_name, page_size=20, operator="contains"):
    """根据名称查询客户公司记录。支持 operator: contains, equals"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_COMPANY}/records_query"
    payload = {
        "select": ["_id", "text_name", "option_status", "option_type"],
        "filter": build_filter("text_name", OBJECT_COMPANY, company_name, operator),
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def list_products(page_size=500):
    """获取产品列表（不带过滤），用于辅助 AI 匹配"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_PRODUCT}/records_query"
    payload = {
        "select": ["_id", "text_name"],
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def list_companies(page_size=500):
    """获取客户公司列表（不带过滤），用于辅助 AI 匹配"""
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_COMPANY}/records_query"
    payload = {
        "select": ["_id", "text_name"],
        "page_size": page_size,
    }
    return api_client.request("POST", path, payload)


def create_ticket(record_data):
    """创建工单记录
    record_data 示例:
    {
        "text_title": "测试工单",
        "lookup_product": {"_id": "prod_123"},
        "option_level": "level_high",
        "datetime_report": 1710582000000,
        "lookup_company": {"_id": "comp_456"},
        "option_type": "type_bug"
    }
    """
    path = f"/v1/data/namespaces/{NAMESPACE}/objects/{OBJECT_TICKET}/records"
    payload = {"record": record_data}
    return api_client.request("POST", path, payload)


def get_ticket_meta():
    """获取工单的可选字段元数据（如工单类型、优先级等级等）"""
    return TICKET_OPTIONS


# 工具说明供 AI 技能使用
TOOLS = [
    {
        "name": "search_product",
        "description": "根据名称查询特定产品的信息。支持 operator 参数进行模糊(contains)或精确(equals)匹配",
    },
    {
        "name": "list_products",
        "description": "获取所有可选产品列表，当需要 AI 自行匹配或辅助用户选择时使用",
    },
    {
        "name": "search_company",
        "description": "根据名称查询特定客户公司的信息。支持 operator 参数进行模糊(contains)或精确(equals)匹配",
    },
    {
        "name": "list_companies",
        "description": "获取所有可选客户公司列表，当需要 AI 自行匹配或辅助用户选择时使用",
    },
    {
        "name": "search_user",
        "description": "根据姓名查询飞书 aPaaS 用户记录。支持 operator 参数进行模糊(contains)或精确(equals)匹配",
    },
    {
        "name": "search_user_by_email",
        "description": "根据邮箱精确查询飞书 aPaaS 用户记录",
    },
    {
        "name": "get_ticket_meta",
        "description": "获取工单创建时支持的选项字段（类型、级别等）及其 API 名称",
    },
    {
        "name": "create_ticket",
        "description": "在管理平台正式创建工单。请求前请确保已通过 search 获取到必要的产品和用户 ID",
    },
]
