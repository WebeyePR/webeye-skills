#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Webeye CSBU AI Skills 管理与统计自动化脚本.

该工具为 Webeye AI Skills 仓库提供核心运维能力：
1. lint: 技能合规性检查（YAML 规范、结构完备性、防泄密与安全扫描）
2. stats: 全景统计报表（状态分布、复杂度、复用度、代码/文档规模）
3. sync: 自动扫描提取元数据，生成/刷新 skills_manifest.json 及同步 SKILLS.md
4. scaffold: 一键创建符合 Webeye 规范的标准化技能脚手架

遵循原则：零外部依赖（纯 Python 标准库）、防泄密（Zero-Leakage）、单一真源（Single Source of Truth）。
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 根目录定位
ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"
MANIFEST_FILE = ROOT_DIR / "skills_manifest.json"
SKILLS_MD_FILE = ROOT_DIR / "SKILLS.md"

# 常量定义
VALID_STATUSES = ["生产可用", "调试中", "开发中", "已废弃"]
VALID_COMPLEXITIES = ["高", "中", "低"]
VALID_REUSABILITIES = ["高", "中", "低"]

# 状态 Emoji 映射
STATUS_EMOJI_MAP = {
    "生产可用": "✅ 生产可用",
    "调试中": "🚧 调试中",
    "开发中": "🚧 开发中",
    "已废弃": "📦 已废弃",
}

# 敏感扩展名与文件名黑名单
SENSITIVE_FILE_NAMES = [".env", ".env.local", ".env.production", ".env.staging"]
SENSITIVE_FILE_EXTENSIONS = [".pem", ".key", ".p12", ".pkcs12", ".cer", ".pfx"]
SENSITIVE_PRIVATE_KEY_FILES = ["id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"]

# 深度安全规则体系 (Zero-Leakage 防泄密检测规则集与处理建议)
SECURITY_RULES = [
    {
        "id": "SEC-001-OPENAI-KEY",
        "category": "AI_API_KEYS",
        "severity": "CRITICAL",
        "name": "OpenAI / Anthropic API Key 泄露",
        "pattern": r"\b(sk-[a-zA-Z0-9_-]{20,}|sk-ant-api[0-9]{2}-[a-zA-Z0-9_-]{30,})\b",
        "remediation": [
            "【紧急】立即登录 OpenAI (platform.openai.com) 或 Anthropic 控制台吊销 (Revoke) 并轮换该 Key。",
            "【改写代码】改用环境变量读取：Python `os.environ.get('OPENAI_API_KEY')`，Node.js `process.env.OPENAI_API_KEY`。",
            "【配置模板】在对应技能目录下更新 `.env.example`，并将真实 `.env` 添加到 `.gitignore` 确保不被提交。",
            "【Git 清理】若已推送至远端仓库，请使用 `git filter-repo` 或 BFG 清除历史 commit 中的敏感记录。",
        ],
    },
    {
        "id": "SEC-002-GOOGLE-KEY",
        "category": "CLOUD_CREDENTIALS",
        "severity": "CRITICAL",
        "name": "Google Cloud / Gemini / Firebase API Key 泄露",
        "pattern": r"\b(AIza[0-9A-Za-z-_]{35})\b",
        "remediation": [
            "【紧急】前往 Google Cloud Console / Firebase 控制台立即停用并删除该凭据。",
            "【改写代码】改用 Application Default Credentials (ADC) 或从环境变量 `GEMINI_API_KEY` 动态读取。",
            "【隔离配置】将敏感配置写入本地 `.env`，并在 `.env.example` 中仅保留 `GEMINI_API_KEY=your_key_here` 占位符。",
        ],
    },
    {
        "id": "SEC-003-AWS-ACCESS-KEY",
        "category": "CLOUD_CREDENTIALS",
        "severity": "CRITICAL",
        "name": "AWS Access Key ID 泄露",
        "pattern": r"\b((?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16})\b",
        "remediation": [
            "【紧急】立即前往 AWS IAM 控制台停用并删除该 Access Key。",
            "【改写代码】改用 IAM 角色授权或本地 `~/.aws/credentials` 文件，代码中通过 `boto3.Session()` 自动获取凭证。",
        ],
    },
    {
        "id": "SEC-004-GIT-TOKEN",
        "category": "GIT_TOKENS",
        "severity": "CRITICAL",
        "name": "GitHub / GitLab 访问令牌 (Token) 泄露",
        "pattern": r"\b(gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{82}|glpat-[a-zA-Z0-9_-]{20,})\b",
        "remediation": [
            "【紧急】立即在 GitHub/GitLab 个人设置 -> Personal Access Tokens 中撤销 (Revoke) 该令牌。",
            "【改写代码】通过环境变量 `GITHUB_TOKEN` 或 Git 凭据管理器读取，严禁硬编码。",
        ],
    },
    {
        "id": "SEC-005-FEISHU-SECRET",
        "category": "IM_SECRETS",
        "severity": "CRITICAL",
        "name": "飞书 / 企业应用 App Secret 泄露",
        "pattern": r"(?i)(?:app_secret|client_secret|bot_secret)\s*[:=]\s*[\"']([a-zA-Z0-9_-]{16,})[\"']",
        "remediation": [
            "【紧急】登录飞书开放平台开发者后台，重置 (Reset) 对应自建应用的 App Secret。",
            "【改写代码】改为从环境变量 `FEISHU_APP_SECRET` 读取。",
            "【配置脱敏】在技能目录下更新 `.env.example` 模板。",
        ],
    },
    {
        "id": "SEC-006-FEISHU-WEBHOOK",
        "category": "WEBHOOKS",
        "severity": "CRITICAL",
        "name": "飞书自定义机器人 Webhook URL 泄露",
        "pattern": r"https://open\.feishu\.cn/open-apis/bot/v2/hook/([a-f0-9-]{36})",
        "remediation": [
            "【紧急】进入飞书群设置 -> 群机器人，重新生成 Webhook 凭证并开启【签名校验】。",
            "【改写代码】提取为环境变量 `FEISHU_WEBHOOK_URL` 动态加载。",
        ],
    },
    {
        "id": "SEC-007-WECHAT-WEBHOOK",
        "category": "WEBHOOKS",
        "severity": "CRITICAL",
        "name": "企业微信机器人 Webhook Key 泄露",
        "pattern": r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=([a-f0-9-]{36})",
        "remediation": [
            "【紧急】进入企业微信群机器人管理，重新生成 Webhook Key。",
            "【改写代码】通过环境变量 `WECHAT_WEBHOOK_KEY` 传入。",
        ],
    },
    {
        "id": "SEC-008-PRIVATE-KEY",
        "category": "PRIVATE_KEYS",
        "severity": "CRITICAL",
        "name": "未脱敏的私钥文本 (Private Key) 泄露",
        "pattern": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        "remediation": [
            "【紧急】立即作废并吊销对应的私钥与公钥证书。",
            "【文件迁移】禁止将私钥直接保存在代码仓库中，应使用密钥管理服务 (Vault / GCP Secret Manager / AWS Secrets Manager) 或通过文件路径注入。",
        ],
    },
    {
        "id": "SEC-009-DATABASE-PASSWORD",
        "category": "DATABASE_CREDS",
        "severity": "HIGH",
        "name": "数据库连接字符串明文密码泄露",
        "pattern": r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis):\/\/[a-zA-Z0-9_.-]+:([^@\s/]+)@[a-zA-Z0-9_.-]+",
        "remediation": [
            "【紧急】立即修改数据库账户密码。",
            "【改写代码】连接字符串改为通过环境变量 `DATABASE_URL` 注入。",
        ],
    },
    {
        "id": "SEC-010-LOCAL-PATH",
        "category": "LOCAL_PATHS",
        "severity": "MEDIUM",
        "name": "开发者本机绝对路径泄露",
        "pattern": r"/(?:Users|home)/[a-zA-Z0-9_.-]+/(?:Project|Git|Desktop|Downloads|Documents|workspace|skills)/[a-zA-Z0-9_./-]+",
        "remediation": [
            "【路径规范】在 Python 中改用相对路径：`Path(__file__).resolve().parent` 或 `ROOT_DIR / '...'`。",
            "【文档链接】在 Markdown 中使用相对链接（如 `./references/foo.md`）而非 `file:///Users/...` 本地路径。",
            "【动态环境变量】若必须跨环境定位，可通过环境变量 `WORKSPACE_DIR` 传入。",
        ],
    },
]

# 常见无害占位符白名单过滤
PLACEHOLDER_SUBSTRINGS = [
    "your_", "example", "placeholder", "xxx", "0000", "dummy", "sample",
    "my_secret", "changeme", "test_key", "secret_key_here", "<token>", "<key>"
]


def is_placeholder(val: str) -> bool:
    """判断字符串是否为常见的文档示例/占位符."""
    val_lower = val.lower()
    return any(p in val_lower for p in PLACEHOLDER_SUBSTRINGS)


def mask_secret(val: str) -> str:
    """对敏感字符串进行掩码脱敏处理 (遵守 Zero-Leakage 打印原则)."""
    if len(val) <= 8:
        return "****"
    prefix = val[:4]
    suffix = val[-4:]
    return f"{prefix}****{suffix}"


def parse_yaml_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """轻量级解析 Markdown 顶部的 YAML Frontmatter（零依赖实现）.

    :param content: Markdown 文件全文
    :return: (元数据字典, 正文内容)
    """
    metadata: Dict[str, Any] = {}
    body = content

    if not content.startswith("---"):
        return metadata, body

    parts = content.split("---", 2)
    if len(parts) < 3:
        return metadata, body

    raw_yaml = parts[1].strip()
    body = parts[2].strip()

    current_key = None
    multiline_buf = []
    is_multiline = False

    for line in raw_yaml.splitlines():
        # 处理多行折叠文本（如 description: > 或 description: |）
        if is_multiline:
            if line.startswith("  ") or line.startswith("\t") or line.strip() == "":
                multiline_buf.append(line.strip())
                continue
            else:
                if current_key:
                    metadata[current_key] = " ".join(multiline_buf).strip()
                multiline_buf = []
                is_multiline = False
                current_key = None

        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()

            if val in [">", "|", ">-", "|-"]:
                current_key = key
                is_multiline = True
                multiline_buf = []
                continue

            # 处理引号
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            elif val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            elif val.isdigit():
                val = int(val)

            metadata[key] = val

    if is_multiline and current_key:
        metadata[current_key] = " ".join(multiline_buf).strip()

    return metadata, body


def parse_skills_md_defaults() -> Dict[str, Dict[str, str]]:
    """从 SKILLS.md 中解析已有技能的默认状态、复杂度和复用度."""
    defaults: Dict[str, Dict[str, str]] = {}
    if not SKILLS_MD_FILE.exists():
        return defaults

    try:
        content = SKILLS_MD_FILE.read_text(encoding="utf-8")
        for line in content.splitlines():
            # 匹配形如 | [**feishu-crm-automation** ...](...) | 🚧 开发中 | 高 | 低 | ...
            m = re.search(r"\|\s*\[\*\*([a-z0-9_-]+)\*\*.*?\]\(.*?\)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|", line)
            if m:
                s_name = m.group(1).strip()
                s_status_raw = m.group(2).strip()
                s_complexity = m.group(3).strip()
                s_reusability = m.group(4).strip()

                s_status = "生产可用"
                for st in VALID_STATUSES:
                    if st in s_status_raw:
                        s_status = st
                        break

                defaults[s_name] = {
                    "status": s_status,
                    "complexity": s_complexity if s_complexity in VALID_COMPLEXITIES else "中",
                    "reusability": s_reusability if s_reusability in VALID_REUSABILITIES else "中",
                }
    except Exception:
        pass
    return defaults


def scan_single_skill(skill_dir: Path, md_defaults: Optional[Dict[str, Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    """扫描并提取单个技能的完整元数据及文件统计."""
    if not skill_dir.is_dir() or skill_dir.name.startswith("."):
        return None

    skill_md = skill_dir / "SKILL.md"
    has_skill_md = skill_md.exists()

    name = skill_dir.name
    fallback = (md_defaults or {}).get(name, {})

    metadata: Dict[str, Any] = {
        "name": name,
        "path": f"skills/{name}",
        "has_skill_md": has_skill_md,
        "title": name,
        "description": "",
        "version": "1.0.0",
        "author": "Webeye CSBU Team",
        "license": "MIT",
        "alwaysActive": False,
        "status": fallback.get("status", "生产可用"),
        "complexity": fallback.get("complexity", "中"),
        "reusability": fallback.get("reusability", "中"),
        "category": "未分类",
        "scripts": [],
        "references": [],
        "tests": [],
        "has_env_example": (skill_dir / ".env.example").exists(),
        "has_requirements": (skill_dir / "requirements.txt").exists(),
        "has_package_json": (skill_dir / "package.json").exists(),
        "total_files": 0,
        "total_lines": 0,
    }

    if has_skill_md:
        try:
            content = skill_md.read_text(encoding="utf-8")
            yaml_meta, body = parse_yaml_frontmatter(content)
            metadata.update(yaml_meta)

            # 提取主标题（若无则使用 name）
            title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            if title_match:
                metadata["title"] = title_match.group(1).strip()
        except Exception as e:
            metadata["error"] = f"解析 SKILL.md 异常: {str(e)}"

    # 扫描子目录和统计文件
    for root, _, files in os.walk(skill_dir):
        for file in files:
            if file == ".DS_Store" or file.endswith(".pyc"):
                continue
            file_path = Path(root) / file
            rel_path = file_path.relative_to(skill_dir).as_posix()
            metadata["total_files"] += 1

            if rel_path.startswith("scripts/"):
                metadata["scripts"].append(rel_path)
            elif rel_path.startswith("references/"):
                metadata["references"].append(rel_path)
            elif rel_path.startswith("tests/"):
                metadata["tests"].append(rel_path)

            try:
                if file_path.stat().st_size < 500_000:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        metadata["total_lines"] += sum(1 for _ in f)
            except Exception:
                pass

    return metadata


def scan_all_skills(skills_dir: Path = SKILLS_DIR) -> List[Dict[str, Any]]:
    """扫描 skills 目录下所有技能."""
    if not skills_dir.exists():
        return []

    md_defaults = parse_skills_md_defaults()
    skills = []
    for item in sorted(skills_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            skill_info = scan_single_skill(item, md_defaults=md_defaults)
            if skill_info:
                skills.append(skill_info)
    return skills


def run_security_scan(skills_dir: Path = SKILLS_DIR, target_skill: Optional[str] = None) -> List[Dict[str, Any]]:
    """深度安全与防泄密扫描引擎.

    扫描密钥、凭据、私钥、明文配置、未脱敏绝对路径等敏感信息，并输出结构化 Findings。
    """
    findings: List[Dict[str, Any]] = []

    skills_to_check = []
    if target_skill:
        target_path = skills_dir / target_skill
        if target_path.exists() and target_path.is_dir():
            skills_to_check.append(target_path)
    else:
        skills_to_check = [p for p in sorted(skills_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")]

    for skill_path in skills_to_check:
        skill_name = skill_path.name

        for root, _, files in os.walk(skill_path):
            for file in files:
                if file.endswith(".pyc") or file == ".DS_Store" or file == ".gitkeep":
                    continue

                file_path = Path(root) / file
                rel_path = file_path.relative_to(skill_path).as_posix()
                file_ext = file_path.suffix.lower()

                # 1. 检查危险文件名 (如真实 .env)
                if file in SENSITIVE_FILE_NAMES:
                    findings.append({
                        "skill": skill_name,
                        "file": rel_path,
                        "line": 1,
                        "rule_id": "SEC-FILE-001",
                        "rule_name": f"发现真实配置文件 '{file}'",
                        "category": "SENSITIVE_FILES",
                        "severity": "CRITICAL",
                        "snippet": f"存在文件: {rel_path}",
                        "remediation": [
                            f"1. 【紧急】运行 `git rm --cached {rel_path}` 将其从 Git 跟踪中移除。",
                            "2. 【配置防漏】确保项目根目录与子目录的 `.gitignore` 包含该文件名。",
                            "3. 【模板替代】创建 `.env.example`，仅保留占位符供开发者参考。",
                        ],
                    })

                # 2. 检查危险文件扩展名 (如私钥、证书文件)
                if file_ext in SENSITIVE_FILE_EXTENSIONS or file in SENSITIVE_PRIVATE_KEY_FILES:
                    findings.append({
                        "skill": skill_name,
                        "file": rel_path,
                        "line": 1,
                        "rule_id": "SEC-FILE-002",
                        "rule_name": f"发现私钥/证书文件 '{file}'",
                        "category": "SENSITIVE_FILES",
                        "severity": "CRITICAL",
                        "snippet": f"存在文件: {rel_path}",
                        "remediation": [
                            f"1. 【紧急】运行 `git rm --cached {rel_path}` 从 Git 中删除敏感文件。",
                            "2. 【证书轮换】若已推送到远端仓库，请立即作废该证书/私钥并重新签发。",
                            "3. 【密钥管理】改用云平台 Secret Manager 动态挂载，严禁将私钥直接提交到代码仓库。",
                        ],
                    })

                # 3. 扫描文件内部文本敏感信息
                try:
                    if file_path.stat().st_size > 1_000_000:
                        continue

                    # 尝试按行读取
                    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for line_idx, line_text in enumerate(lines, start=1):
                        # 如果整行是注释掉的示例且包含明显的占位符，跳过
                        for rule in SECURITY_RULES:
                            matches = re.finditer(rule["pattern"], line_text)
                            for m in matches:
                                raw_val = m.group(0)
                                if is_placeholder(raw_val):
                                    continue

                                # 针对本地路径规则：如果是在 .serena / tests / 或者是文档中提及的合法相对示例，过滤
                                if rule["id"] == "SEC-010-LOCAL-PATH":
                                    if "your_username" in raw_val or "/path/to/" in raw_val:
                                        continue

                                snippet_line = line_text.strip()
                                # 脱敏处理
                                masked_line = snippet_line.replace(raw_val, mask_secret(raw_val))
                                if len(masked_line) > 120:
                                    masked_line = masked_line[:117] + "..."

                                findings.append({
                                    "skill": skill_name,
                                    "file": rel_path,
                                    "line": line_idx,
                                    "rule_id": rule["id"],
                                    "rule_name": rule["name"],
                                    "category": rule["category"],
                                    "severity": rule["severity"],
                                    "snippet": masked_line,
                                    "remediation": rule["remediation"],
                                })
                except Exception:
                    pass

    return findings


def print_security_report(findings: List[Dict[str, Any]], target_skill: Optional[str] = None) -> None:
    """打印精美、详尽的安全审计与防泄密告警报告."""
    print("=" * 76)
    print("  🛡️  Webeye CSBU AI Skills 深度安全与防泄密审计报告")
    print(f"  📅  审计时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if target_skill:
        print(f"  🎯  目标技能: {target_skill}")
    print("=" * 76)

    critical_count = sum(1 for f in findings if f["severity"] == "CRITICAL")
    high_count = sum(1 for f in findings if f["severity"] == "HIGH")
    medium_count = sum(1 for f in findings if f["severity"] == "MEDIUM")
    low_count = sum(1 for f in findings if f["severity"] == "LOW")

    print(f"\n📊 风险等级统计:")
    print(f"  • 🚨 严重 (CRITICAL) : {critical_count} 项")
    print(f"  • ⚠️  高危 (HIGH)     : {high_count} 项")
    print(f"  • 🟡 中危 (MEDIUM)   : {medium_count} 项")
    print(f"  • ℹ️  提示 (LOW)      : {low_count} 项")
    print(f"  • 🔍 发现总数        : {len(findings)} 项\n")

    if not findings:
        print("✨ 恭喜！未检测到任何敏感密钥泄露、未脱敏凭证或路径风险 (0 Security Issues)。\n" + "=" * 76 + "\n")
        return

    # 按技能与风险等级分组展示
    print("-" * 76)
    print("🚨 详细告警列表与建议处理方式:")
    print("-" * 76)

    # 排序：CRITICAL > HIGH > MEDIUM > LOW
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sorted_findings = sorted(findings, key=lambda x: (sev_order.get(x["severity"], 9), x["skill"], x["file"]))

    for idx, f in enumerate(sorted_findings, start=1):
        sev = f["severity"]
        sev_tag = f"[{sev}]"
        if sev == "CRITICAL":
            badge = f"🚨 \033[1;31m{sev_tag}\033[0m" if sys.stdout.isatty() else f"🚨 {sev_tag}"
        elif sev == "HIGH":
            badge = f"⚠️  \033[1;33m{sev_tag}\033[0m" if sys.stdout.isatty() else f"⚠️  {sev_tag}"
        else:
            badge = f"🟡 {sev_tag}"

        loc = f"skills/{f['skill']}/{f['file']}" + (f":{f['line']}" if f.get('line') else "")
        print(f"\n#{idx} {badge} 规则: {f['rule_name']} ({f['rule_id']})")
        print(f"   📍 位置: {loc}")
        print(f"   📝 代码片段 (已脱敏): {f['snippet']}")
        print(f"   💡 建议处理方式 (Remediation):")
        for step in f["remediation"]:
            print(f"      {step}")

    print("\n" + "=" * 76)
    if critical_count > 0 or high_count > 0:
        print("💥 发现 CRITICAL 或 HIGH 级别安全漏洞！请立即按照上述指引处理后再提交代码。")
    print("=" * 76 + "\n")


def run_lint(skills_dir: Path = SKILLS_DIR, target_skill: Optional[str] = None) -> Tuple[bool, List[str], List[str]]:
    """执行合规性检查 (Lint)，包含结构检查、Frontmatter 规范及深度安全扫描.

    :return: (是否全部通过, 错误列表, 警告列表)
    """
    errors: List[str] = []
    warnings: List[str] = []

    skills_to_check = []
    if target_skill:
        target_path = skills_dir / target_skill
        if not target_path.exists():
            errors.append(f"❌ 目标技能不存在: {target_skill}")
            return False, errors, warnings
        skills_to_check.append(target_path)
    else:
        skills_to_check = [p for p in sorted(skills_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")]

    print(f"🔍 正在对 {len(skills_to_check)} 个技能执行合规与深度安全扫描...\n")

    for skill_path in skills_to_check:
        skill_name = skill_path.name
        skill_prefix = f"[{skill_name}]"

        # 1. 目录命名规范 (kebab-case)
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", skill_name):
            errors.append(f"{skill_prefix} 目录名必须遵循 kebab-case 规范 (仅限小写字母、数字和连字符)")

        # 2. SKILL.md 存在性
        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"{skill_prefix} 缺少必需的技能定义文件: SKILL.md")
            continue

        # 3. 解析并检查 Frontmatter
        try:
            content = skill_md.read_text(encoding="utf-8")
            meta, body = parse_yaml_frontmatter(content)

            if not meta:
                warnings.append(f"{skill_prefix} SKILL.md 缺少标准 YAML Frontmatter 头部元数据")

            if "name" in meta and meta["name"] != skill_name:
                errors.append(f"{skill_prefix} YAML 中的 name ('{meta['name']}') 与目录名 ('{skill_name}') 不一致")

            if "version" in meta:
                version_str = str(meta["version"])
                if not re.match(r"^(\d+\.\d+\.\d+|v\d{8})$", version_str):
                    warnings.append(f"{skill_prefix} version ('{version_str}') 建议遵循严格的 SemVer (x.y.z) 或 CalVer (vYYYYMMDD) 格式")

            if "status" in meta and meta["status"] not in VALID_STATUSES:
                warnings.append(f"{skill_prefix} 状态 '{meta['status']}' 建议使用标准值之一: {VALID_STATUSES}")

        except Exception as e:
            errors.append(f"{skill_prefix} 读取/解析 SKILL.md 失败: {e}")

    # 4. 执行深度安全扫描
    findings = run_security_scan(skills_dir=skills_dir, target_skill=target_skill)
    for f in findings:
        loc = f"skills/{f['skill']}/{f['file']}" + (f":{f['line']}" if f.get('line') else "")
        msg = f"[{f['skill']}] {f['rule_name']} ({f['rule_id']}) 在 '{loc}'"
        
        # 严重或高危记为 Error，中低危记为 Warning
        if f["severity"] in ["CRITICAL", "HIGH"]:
            errors.append(msg)
        else:
            warnings.append(msg)

    is_passed = len(errors) == 0
    return is_passed, errors, warnings


def generate_stats(skills: List[Dict[str, Any]]) -> Dict[str, Any]:
    """生成全局统计数据模型."""
    stats: Dict[str, Any] = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_skills": len(skills),
        "status_breakdown": {},
        "complexity_breakdown": {},
        "reusability_breakdown": {},
        "category_breakdown": {},
        "total_files": sum(s.get("total_files", 0) for s in skills),
        "total_lines": sum(s.get("total_lines", 0) for s in skills),
        "total_scripts": sum(len(s.get("scripts", [])) for s in skills),
        "skills_with_tests": sum(1 for s in skills if s.get("tests")),
        "skills_with_env": sum(1 for s in skills if s.get("has_env_example")),
    }

    for s in skills:
        status = s.get("status", "生产可用")
        stats["status_breakdown"][status] = stats["status_breakdown"].get(status, 0) + 1

        complexity = s.get("complexity", "中")
        stats["complexity_breakdown"][complexity] = stats["complexity_breakdown"].get(complexity, 0) + 1

        reusability = s.get("reusability", "中")
        stats["reusability_breakdown"][reusability] = stats["reusability_breakdown"].get(reusability, 0) + 1

        category = s.get("category", "未分类")
        stats["category_breakdown"][category] = stats["category_breakdown"].get(category, 0) + 1

    return stats


def print_stats_report(stats: Dict[str, Any], skills: List[Dict[str, Any]]) -> None:
    """在终端打印精美的统计报表."""
    print("=" * 70)
    print("  📊 Webeye CSBU AI Skills 全景统计报表")
    print(f"  📅 统计时间: {stats['generated_at']}")
    print("=" * 70)

    print(f"\n📦 基础指标:")
    print(f"  • 技能总数 (Internal Skills): {stats['total_skills']}")
    print(f"  • 总文件数: {stats['total_files']} 个")
    print(f"  • 包含独立脚本: {stats['total_scripts']} 个")
    print(f"  • 配套测试覆盖: {stats['skills_with_tests']} / {stats['total_skills']}")
    print(f"  • 提供环境模板 (.env.example): {stats['skills_with_env']} / {stats['total_skills']}")
    print(f"  • 估算代码/文档总行数: {stats['total_lines']} 行")

    print(f"\n🚦 状态分布 (Status):")
    for status, count in stats["status_breakdown"].items():
        emoji = STATUS_EMOJI_MAP.get(status, "🔹")
        pct = (count / stats["total_skills"]) * 100 if stats["total_skills"] else 0
        print(f"  {emoji:14} : {count:2d} ({pct:5.1f}%)")

    print(f"\n🧩 工程复杂度 (Complexity):")
    for comp, count in stats["complexity_breakdown"].items():
        pct = (count / stats["total_skills"]) * 100 if stats["total_skills"] else 0
        print(f"  {comp:8} : {count:2d} ({pct:5.1f}%)")

    print(f"\n🔄 可复用性 (Reusability):")
    for r, count in stats["reusability_breakdown"].items():
        pct = (count / stats["total_skills"]) * 100 if stats["total_skills"] else 0
        print(f"  {r:8} : {count:2d} ({pct:5.1f}%)")

    print("\n" + "-" * 70)
    print(f"{'技能名称':<30} | {'版本':<7} | {'状态':<10} | {'复杂度':<4} | {'复用度':<4}")
    print("-" * 70)
    for s in sorted(skills, key=lambda x: x["name"]):
        name = s["name"]
        ver = str(s.get("version", "1.0.0"))
        status = s.get("status", "生产可用")
        comp = s.get("complexity", "中")
        reuse = s.get("reusability", "中")
        print(f"{name:<30} | {ver:<7} | {status:<10} | {comp:<4} | {reuse:<4}")
    print("=" * 70 + "\n")


def sync_manifest_and_skills(skills: List[Dict[str, Any]]) -> None:
    """将扫描结果持久化到 skills_manifest.json，并验证 SKILLS.md 一致性."""
    manifest_data = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "description": "Webeye CSBU AI Skills 全局技能元数据注册表",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_skills": len(skills),
        "skills": skills,
    }

    # 1. 写入 skills_manifest.json
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已成功更新机器可读注册表: {MANIFEST_FILE.name} (共收录 {len(skills)} 个技能)")

    # 2. 检查 SKILLS.md 是否收录所有物理技能
    if SKILLS_MD_FILE.exists():
        skills_md_content = SKILLS_MD_FILE.read_text(encoding="utf-8")
        missing_in_doc = []
        for s in skills:
            if s["name"] not in skills_md_content:
                missing_in_doc.append(s["name"])

        if missing_in_doc:
            print(f"⚠️  警告: 以下技能存在于 skills/ 目录中，但未在 SKILLS.md 索引中提及: {missing_in_doc}")
        else:
            print(f"✅ SKILLS.md 技能索引覆盖完整，无遗漏项。")


def scaffold_new_skill(
    name: str,
    description: str = "",
    author: str = "Webeye CSBU Team",
    skills_dir: Path = SKILLS_DIR,
) -> Path:
    """一键创建符合规范的标准技能脚手架."""
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        print(f"❌ 错误: 技能名称 '{name}' 不合规，必须遵循 kebab-case 规范 (仅限小写字母、数字和连字符)！")
        sys.exit(1)

    target_dir = skills_dir / name
    if target_dir.exists():
        print(f"❌ 错误: 技能目录 '{name}' 已存在: {target_dir}")
        sys.exit(1)

    # 创建标准目录层级
    (target_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (target_dir / "references").mkdir(parents=True, exist_ok=True)
    (target_dir / "tests").mkdir(parents=True, exist_ok=True)

    # 占位 .gitkeep
    (target_dir / "scripts" / ".gitkeep").touch()
    (target_dir / "references" / ".gitkeep").touch()
    (target_dir / "tests" / ".gitkeep").touch()

    # 创建 .env.example
    env_example = target_dir / ".env.example"
    env_example.write_text(
        "# 环境变量配置示例 (请复制为 .env 并填写实际值，绝不要提交真实密钥至 Git 仓库)\n# EXAMPLE_API_KEY=your_key_here\n",
        encoding="utf-8",
    )

    # 创建标准 SKILL.md
    skill_md = target_dir / "SKILL.md"
    desc_str = description or f"{name} 技能描述与使用说明。"
    skill_md_content = f"""---
name: {name}
description: >
  {desc_str}
version: 1.0.0
author: {author}
license: MIT
alwaysActive: false
status: 开发中
complexity: 中
reusability: 高
category: 业务自动化
---

# 🚀 {name}

## 📌 定角色 (WHO)
你是一个专注于业务提效的专业助手，严格遵循 SOP 执行链路。

## 📌 定场景 (WHEN)
- 触发关键词：`/{name}` 或用户提出相关业务需求时。

## 🎯 立目标 (WHAT)
- 核心输出：提供结构化的处理结果。

## ⚙️ 理规则 (HOW)
1. **第一步：输入解析**：提取关键实体并进行格式校验。
2. **第二步：挂起预览**：展示预览草案，等待用户确认。
3. **第三步：正式执行**：获得确认后调用工具执行。

## 📖 给示例 (REFERENCE)
> 示例说明与参考模板。

## 🚫 划边界 (LIMITS)
- 禁止在未获得用户确认前擅自执行高危操作。
- 严禁在对话日志中泄露任何 Token 或密钥。
"""
    skill_md.write_text(skill_md_content, encoding="utf-8")

    def _fmt_path(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT_DIR))
        except ValueError:
            return str(p)

    print(f"🎉 成功创建标准化技能脚手架: {target_dir}")
    print(f"📁 包含文件:")
    print(f"  • {_fmt_path(skill_md)}")
    print(f"  • {_fmt_path(env_example)}")
    print(f"  • {_fmt_path(target_dir / 'scripts')}")
    print(f"  • {_fmt_path(target_dir / 'references')}")
    print(f"  • {_fmt_path(target_dir / 'tests')}")
    print(f"\n💡 下一步：请编写具体的业务逻辑与测试，随后运行 `python scripts/manage_skills.py lint` 验证。")
    return target_dir


def main():
    parser = argparse.ArgumentParser(
        description="Webeye CSBU AI Skills 统一管理与统计工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="操作指令")

    # security 子命令
    sec_parser = subparsers.add_parser("security", help="执行深度安全审计与防泄密扫描")
    sec_parser.add_argument("--skill", type=str, help="指定仅检查某个特定技能")
    sec_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出安全审计结果")
    sec_parser.add_argument("--strict", action="store_true", help="严格模式（出现任何级别的风险均返回失败退出码）")

    # lint 子命令
    lint_parser = subparsers.add_parser("lint", help="执行全量合规性与安全性扫描")
    lint_parser.add_argument("--skill", type=str, help="指定仅检查某个特定技能")

    # stats 子命令
    stats_parser = subparsers.add_parser("stats", help="生成并打印全局统计报表")
    stats_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出报表")

    # sync 子命令
    subparsers.add_parser("sync", help="全量同步技能元数据至 skills_manifest.json 并核对 SKILLS.md")

    # scaffold 子命令
    scaffold_parser = subparsers.add_parser("scaffold", help="创建标准化技能脚手架")
    scaffold_parser.add_argument("name", type=str, help="技能名称 (kebab-case)")
    scaffold_parser.add_argument("--desc", type=str, default="", help="技能简述")
    scaffold_parser.add_argument("--author", type=str, default="Webeye CSBU Team", help="技能作者")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "security":
        findings = run_security_scan(SKILLS_DIR, target_skill=args.skill)
        if args.json:
            print(json.dumps(findings, ensure_ascii=False, indent=2))
        else:
            print_security_report(findings, target_skill=args.skill)

        critical_or_high = any(f["severity"] in ["CRITICAL", "HIGH"] for f in findings)
        if (args.strict and findings) or critical_or_high:
            sys.exit(1)
        sys.exit(0)

    elif args.command == "lint":
        passed, errors, warnings = run_lint(SKILLS_DIR, target_skill=args.skill)
        if warnings:
            print("⚠️  警告事项 (Warnings):")
            for w in warnings:
                print(f"  • {w}")
            print()

        if errors:
            print("❌ 错误事项 (Errors):")
            for e in errors:
                print(f"  • {e}")
            print(f"\n💥 Lint 未通过，共发现 {len(errors)} 处严重错误！请运行 `python scripts/manage_skills.py security` 查看详细安全修复建议。")
            sys.exit(1)
        else:
            print("✨ 恭喜！所有合规与安全扫描项均已通过 (0 Errors)。")

    elif args.command == "stats":
        skills = scan_all_skills(SKILLS_DIR)
        stats = generate_stats(skills)
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            print_stats_report(stats, skills)

    elif args.command == "sync":
        skills = scan_all_skills(SKILLS_DIR)
        sync_manifest_and_skills(skills)

    elif args.command == "scaffold":
        scaffold_new_skill(name=args.name, description=args.desc, author=args.author)


if __name__ == "__main__":
    main()
