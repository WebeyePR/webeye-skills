import os
import sys

# 路径保护：确保 scripts 目录在 sys.path 中
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
scripts_dir = os.path.join(project_root, "scripts")
if scripts_dir not in sys.path:
    sys.path.append(scripts_dir)

# 确保在 tests 目录下运行也能加载根目录的 .env
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(project_root, ".env"))

from actions import (  # noqa: E402
    search_product,
    search_user,
    search_user_by_email,
    search_company,
    list_products,
    list_companies,
    create_ticket,
    api_client,
)
from processor import enrich_record  # noqa: E402


def test_connectivity(user_name="测试用户"):
    print("=== 🔍 开始飞书 aPaaS API 连通性测试 ===")

    # 1. 测试认证
    print("\n[1/3] 正在测试身份认证...")
    try:
        token = api_client._get_app_token()
        print(f"✅ 认证成功! Token 获取正常 (前缀: {token[:10]}...)")
    except Exception as e:
        print(f"❌ 认证失败: {e}")
        return None

    # 2. 测试查询产品
    print("\n[2/3] 正在测试产品查询接口...")
    prod_res = search_product("")
    product_item = None
    if prod_res.get("code") == "0":
        items = prod_res.get("data", {}).get("items", [])
        print(f"✅ 查询成功! 找到 {len(items)} 个产品记录。")
        if items:
            product_item = items[0]
            print(
                f"   示例数据: {product_item.get('text_name')} (ID: {product_item.get('_id')})"
            )
    else:
        print(f"❌ 查询产品失败: {prod_res.get('msg')}")

    # 3. 测试查询用户
    print("\n[3/3] 正在测试用户查询接口...")
    user_res = search_user(user_name)
    user_item = None
    if user_res.get("code") == "0":
        items = user_res.get("data", {}).get("items", [])
        print(f"✅ 查询成功! 找到 {len(items)} 个用户记录。")
        if items:
            user_item = items[0]
            # 兼容多语名称展示
            name_data = user_item.get("_name", {})
            display_name = (
                name_data.get("zh_cn") if isinstance(name_data, dict) else name_data
            )
            print(f"   示例用户: {display_name} (ID: {user_item.get('_id')})")
    else:
        print(f"❌ 查询用户失败: {user_res.get('msg')}")

    # 4. 测试邮箱查询接口
    if user_item and user_item.get("_email"):
        print("\n[4/5] 正在测试邮箱搜索接口...")
        email = user_item["_email"]
        email_res = search_user_by_email(email)
        if email_res.get("code") == "0":
            e_items = email_res.get("data", {}).get("items", [])
            if e_items:
                print(f"✅ 邮箱搜索成功! 匹配到用户: {email}")
            else:
                print(f"❌ 邮箱搜索未找到结果: {email}")
        else:
            print(f"❌ 邮箱搜索接口报错: {email_res.get('msg')}")

    # 5. 测试客户公司查询接口
    print("\n[5/7] 正在测试客户公司查询接口...")
    comp_res = search_company("")
    company_item = None
    if comp_res.get("code") == "0":
        items = comp_res.get("data", {}).get("items", [])
        print(f"✅ 查询成功! 找到 {len(items)} 个客户公司记录。")
        if items:
            company_item = items[0]
            print(
                f"   示例数据: {company_item.get('text_name')} (ID: {company_item.get('_id')})"
            )
    else:
        print(f"❌ 查询客户公司失败: {comp_res.get('msg')}")

    # 6. 测试获取全量产品列表
    print("\n[6/7] 正在测试全量产品列表接口...")
    list_prod_res = list_products(page_size=500)
    if list_prod_res.get("code") == "0":
        items = list_prod_res.get("data", {}).get("items", [])
        print(f"✅ 获取成功! 拿到 {len(items)} 条产品参考数据。")
    else:
        print(f"❌ 获取产品列表失败: {list_prod_res.get('msg')}")

    # 7. 测试获取全量公司列表
    print("\n[7/7] 正在测试全量公司列表接口...")
    list_comp_res = list_companies(page_size=1)
    print
    if list_comp_res.get("code") == "0":
        items = list_comp_res.get("data", {}).get("items", [])
        print(f"✅ 获取成功! 拿到 {len(items)} 条公司参考数据。")
    else:
        print(f"❌ 获取公司列表失败: {list_comp_res.get('msg')}")

    return {"product": product_item, "user": user_item, "company": company_item}


def test_create_record_flow(available_data):
    """测试从文本解析到创建记录的完整流程"""
    print("\n=== 📝 开始工单创建全流程测试 ===")

    if not available_data.get("product") or not available_data.get("user"):
        print("❌ 缺少必要测试数据，无法执行创建测试。")
        return

    # 模拟 AI 提取出的原始数据
    user_name = available_data["user"].get("_name", {}).get("zh_cn", "测试用户")
    product_name = available_data["product"].get("text_name", "测试产品")
    company_name = (
        available_data["company"].get("text_name", "测试公司")
        if available_data.get("company")
        else "测试公司"
    )

    raw_data = {
        "date": "2026/03/17",
        "customer": company_name,
        "product": product_name,
        "assignee": user_name,
        "content": "这是一条由自动化测试脚本生成的跟进记录的内容部分。",
        "next_plan": "下周一进行第二次技术对接。",
    }

    print(f"1. 原始解析数据: {raw_data}")

    # 1. 调用 processor 进行数据富化
    print("2. 正在进行数据富化与 ID 补全...")
    enriched_data = enrich_record(raw_data)

    # 手动处理必填的客户公司 ID（测试环境下假设与产品查出来的 ID 一致或使用占位）
    if "lookup_company" not in enriched_data:
        enriched_data["lookup_company"] = {"_id": available_data["product"].get("_id")}

    print(
        f"   富化后的数据概要: Title={enriched_data.get('text_title')}, Time={enriched_data.get('datetime_report')}"
    )

    # 2. 调用动作创建记录
    print("3. 正在提交到飞书 aPaaS...")
    res = create_ticket(enriched_data)

    if res.get("code") == "0":
        print(f"✅ 工单创建成功! 新记录 ID: {res.get('data', {}).get('id')}")
    else:
        print(f"❌ 工单创建失败: {res.get('msg')}")
        if "data" in res:
            print(f"   详情: {res['data']}")


if __name__ == "__main__":
    # 检查 .env
    env_path = os.path.join(project_root, ".env")
    if not os.path.exists(env_path):
        print(f"⚠️ 未发现 .env 文件 ({env_path})。请确保已配置。")
    else:
        data = test_connectivity("曹培亚")
        print(data)
        if data:
            # 执行创建测试
            test_create_record_flow(data)
            print("\n=== ✨ 全部测试完成 ===")
