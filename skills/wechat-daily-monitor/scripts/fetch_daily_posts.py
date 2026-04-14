#!/usr/bin/env python3
"""
微信公众号每日更新监控脚本
调用极致了 API 获取指定公众号的当天发文情况
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# API 配置
API_BASE = "https://www.dajiala.com"


def load_env(env_dir: str = None) -> None:
    """
    安全地加载 .env 文件到环境变量

    Args:
        env_dir: .env 文件所在目录，默认为脚本目录
    """
    if env_dir is None:
        env_dir = os.path.dirname(os.path.abspath(__file__))
        # 如果在 scripts 子目录，向上找一级
        if os.path.basename(env_dir) == 'scripts':
            env_dir = os.path.dirname(env_dir)

    env_path = os.path.join(env_dir, '.env')

    if not os.path.exists(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith('#'):
                continue

            # 解析 KEY=VALUE 格式
            if '=' not in line:
                continue

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()

            # 去除引号（支持单引号、双引号）
            if len(value) >= 2:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]

            # 安全地设置环境变量（只允许字母、数字、下划线）
            if key and key.replace('_', '').isalnum():
                os.environ[key] = value


def get_api_key() -> str:
    """
    获取 API Key，优先级：命令行参数 > .env 文件 > 环境变量
    """
    # 先加载 .env 文件
    load_env()
    return os.environ.get("JIZHILE_API_KEY", os.environ.get("DAJIALA_API_KEY", ""))


def fetch_daily_posts(account_name: str, api_key: str) -> dict:
    """
    获取公众号当天发文情况

    Args:
        account_name: 公众号名称
        api_key: API密钥

    Returns:
        包含文章列表和账号信息的字典
    """
    url = f"{API_BASE}/fbmain/monitor/v3/post_condition"
    payload = {
        "name": account_name,
        "key": api_key,
        "verifycode": ""
    }

    data = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    req = Request(url, data=data, headers=headers, method='POST')

    try:
        with urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))

            response_data = {
                "account_name": account_name,
                "success": result.get("code") == 0,
                "message": result.get("msg", ""),
                "articles": [],
                "total_count": 0
            }

            if result.get("code") == 0:
                raw_articles = result.get("data", [])
                today_str = datetime.now().strftime("%Y-%m-%d")
                
                # 过滤出当天的文章（API有时会把前几天的历史记录一并带回）
                articles = [a for a in raw_articles if a.get("post_time_str", "").startswith(today_str)]
                
                response_data["articles"] = articles
                response_data["total_count"] = len(articles)

                # 添加账号信息（如果有）
                if "mp_nickname" in result:
                    response_data["nickname"] = result["mp_nickname"]
                if "head_img" in result:
                    response_data["avatar"] = result["head_img"]
                if "mp_wxid" in result:
                    response_data["wxid"] = result["mp_wxid"]

            else:
                response_data["error_code"] = result.get("code")
                response_data["error_msg"] = result.get("msg")

            return response_data

    except HTTPError as e:
        return {
            "account_name": account_name,
            "success": False,
            "error": f"HTTP Error: {e.code}",
            "articles": [],
            "total_count": 0
        }
    except URLError as e:
        return {
            "account_name": account_name,
            "success": False,
            "error": f"URL Error: {e.reason}",
            "articles": [],
            "total_count": 0
        }
    except Exception as e:
        return {
            "account_name": account_name,
            "success": False,
            "error": str(e),
            "articles": [],
            "total_count": 0
        }


def fetch_all_accounts(accounts: list, api_key: str) -> dict:
    """
    获取所有公众号的当天发文情况

    Args:
        accounts: 公众号名称列表
        api_key: API密钥

    Returns:
        汇总结果
    """
    results = []

    for account in accounts:
        account = account.strip()
        if not account:
            continue

        print(f"正在获取 {account} 的发文情况...", file=sys.stderr)
        result = fetch_daily_posts(account, api_key)
        results.append(result)

        # 账号间延迟，避免 QPS 超限（≤5次/秒）
        time.sleep(1.2)

    # 统计汇总
    total_accounts = len(results)
    updated_accounts = sum(1 for r in results if r["success"] and r["total_count"] > 0)
    total_articles = sum(r["total_count"] for r in results)

    return {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_accounts": total_accounts,
        "updated_accounts": updated_accounts,
        "total_articles": total_articles,
        "accounts": results
    }


def load_config(config_path: str) -> list:
    """从配置文件加载公众号列表"""
    accounts = []
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    accounts.append(line)
    except FileNotFoundError:
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
    return accounts


def main():
    parser = argparse.ArgumentParser(
        description='监控微信公众号每日更新情况',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认从 skill 目录下的 watchlist.txt 读取监控名单
  %(prog)s

  # 输出 JSON 到文件
  %(prog)s -o daily_report.json

注意：
  - 脚本始终以 watchlist.txt 为唯一数据源
  - watchlist.txt 中只支持公众号名称，不支持 biz 或 URL
        """
    )

    parser.add_argument('--key', help='API密钥（默认自动加载.env文件或环境变量JIZHILE_API_KEY/DAJIALA_API_KEY）')
    parser.add_argument('--output', '-o', help='输出文件路径（默认输出到stdout）')

    args = parser.parse_args()

    # 获取公众号列表
    accounts = []

    # 强制以 watchlist.txt 作为唯一真实数据源
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'watchlist.txt')
    if not os.path.exists(config_path):
        print(f"错误：唯一数据源 {config_path} 不存在", file=sys.stderr)
        sys.exit(1)

    accounts = load_config(config_path)

    # 校验：只支持公众号名称
    for account in accounts:
        if account.startswith('http://') or account.startswith('https://'):
            print(f"错误：watchlist.txt 不支持 URL（{account}），请使用公众号名称", file=sys.stderr)
            sys.exit(1)

    # 去重
    accounts = list(dict.fromkeys(a.strip() for a in accounts if a.strip()))

    if not accounts:
        print("错误：watchlist.txt 为空，请先添加至少一个公众号", file=sys.stderr)
        sys.exit(1)

    api_key = args.key or get_api_key()
    if not api_key:
        print("错误：未配置 API Key，请设置 JIZHILE_API_KEY 环境变量或在 skill 目录 .env 文件中配置", file=sys.stderr)
        sys.exit(1)

    # 获取数据
    result = fetch_all_accounts(accounts, api_key)

    # 输出
    output_data = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output_data)
        print(f"\n已保存监控结果到 {args.output}", file=sys.stderr)
    else:
        print(output_data)

    print(f"\n监控完成：{result['updated_accounts']}/{result['total_accounts']} 个账号有更新，共 {result['total_articles']} 篇文章", file=sys.stderr)


if __name__ == '__main__':
    main()
