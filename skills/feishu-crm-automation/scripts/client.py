import requests
import time
import sys
import os

# 路径保护：确保脚本所在目录在 sys.path 中，支持直接运行
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from config import CLIENT_ID, CLIENT_SECRET, AUTH_URL, BASE_URL  # noqa: E402


class FeishuApaasClient:
    def __init__(self):
        self.client_id = CLIENT_ID
        self.client_secret = CLIENT_SECRET
        self.token = None
        self.expire_time = 0

    def _get_app_token(self):
        """获取并刷新 App Token (有效时间 2 小时)"""
        # 如果 Token 未过期，直接返回
        if self.token and time.time() < self.expire_time:
            return self.token

        payload = {"clientId": self.client_id, "clientSecret": self.client_secret}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(AUTH_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("code") == "0":
                self.token = data["data"]["accessToken"]
                # 提前 5 分钟刷新以防万一
                self.expire_time = time.time() + data["data"]["expireTime"] - 300
                return self.token
            else:
                raise Exception(f"获取 Token 失败: {data.get('msg')}")
        except Exception as e:
            print(f"Auth Error: {e}")
            raise

    def request(self, method, path, json_data=None):
        """通用请求封装，自动注入 Authorization"""
        url = f"{BASE_URL}{path}"
        token = self._get_app_token()

        headers = {"Authorization": token, "Content-Type": "application/json"}

        try:
            response = requests.request(method, url, headers=headers, json=json_data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error: {e.response.text}")
            return e.response.json()
        except Exception as e:
            print(f"Request Error: {e}")
            return {"code": "-1", "msg": str(e)}


# 单例客户端
api_client = FeishuApaasClient()
