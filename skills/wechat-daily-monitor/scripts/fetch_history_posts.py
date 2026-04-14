#!/usr/bin/env python3
"""
微信公众号历史文章获取脚本
调用极致了 API 获取指定公众号的历史发文列表，支持翻页
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

# 可重试的状态码
RETRY_CODES = {-1, 111, 112, 2003, 2005}


def load_env(env_dir: str = None) -> None:
    """加载 .env 文件到环境变量"""
    if env_dir is None:
        env_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.basename(env_dir) == 'scripts':
            env_dir = os.path.dirname(env_dir)

    env_path = os.path.join(env_dir, '.env')
    if not os.path.exists(env_path):
        return

    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
            if key and key.replace('_', '').isalnum():
                os.environ[key] = value


def get_api_key() -> str:
    """获取 API Key，优先级：命令行参数 > .env 文件 > 环境变量"""
    load_env()
    return os.environ.get("JIZHILE_API_KEY", os.environ.get("DAJIALA_API_KEY", ""))


def fetch_history_page(account_name: str, api_key: str, page: int = 1) -> dict:
    """
    获取公众号历史发文列表的指定页

    Args:
        account_name: 公众号名称
        api_key: API密钥
        page: 页码（从1开始，每页5次发文）

    Returns:
        API 原始响应字典
    """
    url = f"{API_BASE}/fbmain/monitor/v3/post_history"
    payload = {
        "name": account_name,
        "key": api_key,
        "verifycode": "",
        "page": str(page)
    }

    data = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    req = Request(url, data=data, headers=headers, method='POST')

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                code = result.get("code", -1)

                # 可重试的错误码
                if code in RETRY_CODES and attempt < max_retries - 1:
                    wait = 2 * (attempt + 1)
                    print(f"  状态码 {code}，{wait}秒后重试 ({attempt+1}/{max_retries})...", file=sys.stderr)
                    time.sleep(wait)
                    # 重建请求（urlopen 会消耗 body）
                    req = Request(url, data=data, headers=headers, method='POST')
                    continue

                result["_account_name"] = account_name
                return result

        except HTTPError as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                req = Request(url, data=data, headers=headers, method='POST')
                continue
            return {"code": -999, "msg": f"HTTP Error: {e.code}", "_account_name": account_name}
        except URLError as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                req = Request(url, data=data, headers=headers, method='POST')
                continue
            return {"code": -999, "msg": f"URL Error: {e.reason}", "_account_name": account_name}
        except Exception as e:
            return {"code": -999, "msg": str(e), "_account_name": account_name}

    return {"code": -999, "msg": "重试次数耗尽", "_account_name": account_name}


def fetch_history_posts(account_name: str, api_key: str, max_pages: int = 0) -> dict:
    """
    获取公众号全部历史发文列表（自动翻页）

    Args:
        account_name: 公众号名称
        api_key: API密钥
        max_pages: 最大翻页数，0表示获取全部

    Returns:
        包含全部历史文章和账号信息的字典
    """
    # 获取第一页
    first_result = fetch_history_page(account_name, api_key, page=1)
    first_result.pop("_account_name", None)
    code = first_result.get("code", -1)

    response_data = {
        "account_name": account_name,
        "success": code == 0,
        "message": first_result.get("msg", ""),
        "articles": [],
        "total_num": 0,
        "total_page": 0,
        "now_page": 0,
        "cost_money": 0.0,
        "remain_money": 0.0
    }

    # 处理失败情况
    if code != 0:
        response_data["error_code"] = code
        response_data["error_msg"] = first_result.get("msg", "")
        return response_data

    # 填充第一页数据
    articles = first_result.get("data", [])
    total_page = first_result.get("total_page", 1)
    total_num = first_result.get("total_num", 0)

    response_data["articles"] = articles
    response_data["total_num"] = total_num
    response_data["total_page"] = total_page
    response_data["now_page"] = first_result.get("now_page", 1)
    response_data["now_page_articles_num"] = first_result.get("now_page_articles_num", len(articles))
    response_data["publish_count"] = first_result.get("publish_count", 0)
    response_data["masssend_count"] = first_result.get("masssend_count", 0)
    response_data["cost_money"] = first_result.get("cost_money", 0)
    response_data["remain_money"] = first_result.get("remain_money", 0)

    # 账号信息（仅 name 参数时返回）
    for field in ["mp_nickname", "mp_wxid", "mp_ghid", "head_img"]:
        if field in first_result:
            response_data[field] = first_result[field]

    print(f"  第 1/{total_page} 页，获得 {len(articles)} 篇文章", file=sys.stderr)

    # 计算实际需要获取的页数
    pages_to_fetch = total_page if max_pages == 0 else min(max_pages, total_page)

    # 自动翻页获取剩余页面
    for page in range(2, pages_to_fetch + 1):
        # QPS 限制：不超过5次/秒，间隔至少 1.2 秒
        time.sleep(1.2)

        page_result = fetch_history_page(account_name, api_key, page=page)
        page_result.pop("_account_name", None)
        page_code = page_result.get("code", -1)

        if page_code != 0:
            # 翻页错误（如 110=没有更多文章），停止翻页
            print(f"  第 {page} 页获取结束: {page_result.get('msg', '')}", file=sys.stderr)
            break

        page_articles = page_result.get("data", [])
        response_data["articles"].extend(page_articles)
        response_data["now_page"] = page
        response_data["cost_money"] += page_result.get("cost_money", 0)
        response_data["remain_money"] = page_result.get("remain_money", response_data["remain_money"])

        print(f"  第 {page}/{total_page} 页，获得 {len(page_articles)} 篇文章（累计 {len(response_data['articles'])} 篇）", file=sys.stderr)

        # 如果该页无文章，停止
        if not page_articles:
            break

    return response_data


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
        description='获取微信公众号历史发文列表',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认从 skill 目录下的 watchlist.txt 读取监控名单
  %(prog)s

  # 只获取 watchlist 中每个账号的前 5 页
  %(prog)s --max-pages 5

  # 获取 watchlist 中每个账号的指定页码
  %(prog)s --page 3

注意：
  - 历史接口按页计费，0.08元/页，每页返回5次发文（每次1~8篇）
  - QPS限制：不超过5次/秒
  - 批量获取历史文章费用较高，请谨慎使用
  - 脚本始终以 watchlist.txt 为唯一数据源，只支持公众号名称
        """
    )

    parser.add_argument('--page', type=int, default=0, help='只获取指定页码（默认自动翻页获取全部）')
    parser.add_argument('--max-pages', type=int, default=1, help='最大翻页数（默认1页，设为0获取全部）')
    parser.add_argument('--key', help='API密钥')
    parser.add_argument('--output', '-o', help='输出文件路径（默认stdout）')

    args = parser.parse_args()

    # 确定 API Key
    api_key = args.key or get_api_key()
    if not api_key:
        print("错误：未配置 API Key，请设置 JIZHILE_API_KEY 环境变量或在 .env 文件中配置", file=sys.stderr)
        sys.exit(1)

    # 强制以 watchlist.txt 作为唯一真实数据源
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.txt")
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

    # 获取数据
    all_results = []

    for i, account in enumerate(accounts):
        print(f"\n[{i+1}/{len(accounts)}] 正在获取 {account} 的历史文章...", file=sys.stderr)

        if args.page:
            # 只获取指定页
            result_raw = fetch_history_page(account, api_key, page=args.page)
            result_raw.pop("_account_name", None)
            code = result_raw.get("code", -1)

            result = {
                "account_name": account,
                "success": code == 0,
                "message": result_raw.get("msg", ""),
                "articles": result_raw.get("data", []) if code == 0 else [],
                "total_num": result_raw.get("total_num", 0),
                "total_page": result_raw.get("total_page", 0),
                "now_page": result_raw.get("now_page", args.page),
                "cost_money": result_raw.get("cost_money", 0),
                "remain_money": result_raw.get("remain_money", 0),
            }
            if code != 0:
                result["error_code"] = code
                result["error_msg"] = result_raw.get("msg", "")
        else:
            # 自动翻页
            result = fetch_history_posts(account, api_key, max_pages=args.max_pages)

        all_results.append(result)

        # 账号间延迟
        if i < len(accounts) - 1:
            time.sleep(1.5)

    # 汇总输出
    output_data = {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_accounts": len(all_results),
        "total_articles": sum(len(r.get("articles", [])) for r in all_results),
        "total_cost": sum(r.get("cost_money", 0) for r in all_results),
        "accounts": all_results
    }

    json_output = json.dumps(output_data, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(json_output)
        print(f"\n已保存到 {args.output}", file=sys.stderr)
    else:
        print(json_output)

    # 汇总统计
    success_count = sum(1 for r in all_results if r.get("success"))
    total_articles = output_data["total_articles"]
    print(f"\n获取完成：{success_count}/{len(all_results)} 个账号成功，共 {total_articles} 篇文章，花费 {output_data['total_cost']:.2f} 元", file=sys.stderr)


if __name__ == '__main__':
    main()
