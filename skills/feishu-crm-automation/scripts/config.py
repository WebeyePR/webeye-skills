import os
from dotenv import load_dotenv

# 加载 .env 文件（如果存在）
load_dotenv()

# 飞书 aPaaS API 基础配置
# 优先级：环境变量 > .env 文件
CLIENT_ID = os.getenv("FEISHU_CLIENT_ID")
CLIENT_SECRET = os.getenv("FEISHU_CLIENT_SECRET")

# 飞书 aPaaS 域名
BASE_URL = "https://ae-openapi.feishu.cn"
# 应用环境配置
NAMESPACE = os.getenv("FEISHU_NAMESPACE", "ticket_management__c")

# 对象 API 名称
OBJECT_TICKET = os.getenv("FEISHU_TICKET_OBJ", "object_order")
OBJECT_PRODUCT = os.getenv("FEISHU_PRODUCT_OBJ", "object_product")
OBJECT_USER = os.getenv("FEISHU_USER_OBJ", "_user")
OBJECT_COMPANY = os.getenv("FEISHU_COMPANY_OBJ", "object_customer_company")

# 认证配置接口
AUTH_URL = f"{BASE_URL}/auth/v1/appToken"
