#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Webeye CSBU AI Skills 管理脚本单元测试套件."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

# 导入待测模块
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from manage_skills import (
    parse_yaml_frontmatter,
    scan_single_skill,
    generate_stats,
    run_lint,
    run_security_scan,
    mask_secret,
    is_placeholder,
    scaffold_new_skill,
)


class TestManageSkills(unittest.TestCase):
    """测试技能管理与统计的核心功能."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_webeye_skills_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_yaml_frontmatter(self):
        """测试 YAML Frontmatter 的解析正确性."""
        sample_md = """---
name: test-skill
description: >
  这是一个多行
  描述测试。
version: 1.2.3
alwaysActive: true
status: 生产可用
complexity: 高
reusability: 中
---

# Test Skill Title

正文内容。
"""
        meta, body = parse_yaml_frontmatter(sample_md)
        self.assertEqual(meta.get("name"), "test-skill")
        self.assertEqual(meta.get("version"), "1.2.3")
        self.assertTrue(meta.get("alwaysActive"))
        self.assertEqual(meta.get("status"), "生产可用")
        self.assertEqual(meta.get("complexity"), "高")
        self.assertIn("这是一个多行 描述测试。", meta.get("description", ""))
        self.assertIn("# Test Skill Title", body)

    def test_scaffold_and_lint(self):
        """测试脚手架创建与 Lint 校验."""
        skill_name = "test-demo-skill"

        scaffold_new_skill(name=skill_name, description="测试脚手架", author="Test Team", skills_dir=self.test_dir)

        created_dir = self.test_dir / skill_name
        self.assertTrue(created_dir.exists())
        self.assertTrue((created_dir / "SKILL.md").exists())
        self.assertTrue((created_dir / ".env.example").exists())
        self.assertTrue((created_dir / "scripts").exists())
        self.assertTrue((created_dir / "references").exists())
        self.assertTrue((created_dir / "tests").exists())

        # 测试 Lint 能够通过
        passed, errors, warnings = run_lint(self.test_dir)
        self.assertTrue(passed, f"Lint 应该通过，但报错: {errors}")
        self.assertEqual(len(errors), 0)

    def test_scan_and_stats(self):
        """测试技能扫描与统计数据计算."""
        # 构建一个模拟技能目录
        skill_dir = self.test_dir / "mock-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: mock-skill
description: 模拟技能
version: 2.0.0
status: 生产可用
complexity: 低
reusability: 高
---
# Mock Skill
""",
            encoding="utf-8",
        )
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "run.py").write_text("print('hello')", encoding="utf-8")

        info = scan_single_skill(skill_dir)
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "mock-skill")
        self.assertEqual(info["version"], "2.0.0")
        self.assertEqual(len(info["scripts"]), 1)

        stats = generate_stats([info])
        self.assertEqual(stats["total_skills"], 1)
        self.assertEqual(stats["status_breakdown"].get("生产可用"), 1)
        self.assertEqual(stats["complexity_breakdown"].get("低"), 1)
        self.assertEqual(stats["reusability_breakdown"].get("高"), 1)

    def test_security_audit_detection_and_remediation(self):
        """测试安全扫描引擎对敏感信息泄漏的精准检测与修复建议提供."""
        leak_skill = self.test_dir / "leak-skill"
        leak_skill.mkdir(parents=True)
        (leak_skill / "SKILL.md").write_text(
            """---
name: leak-skill
description: 存在敏感信息泄露的测试技能
version: 1.0.0
---
# Leak Test
""",
            encoding="utf-8",
        )
        (leak_skill / "scripts").mkdir()
        # 1. 写入真实格式 API Key
        (leak_skill / "scripts" / "api.py").write_text(
            "OPENAI_KEY = 'sk-1234567890abcdef1234567890abcdef'\n"
            "GOOGLE_KEY = 'AIzaSyD12345678901234567890123456789012'\n",
            encoding="utf-8",
        )
        # 2. 写入敏感扩展名文件
        (leak_skill / "private.key").write_text("dummy key", encoding="utf-8")
        # 3. 写入真实 .env 文件
        (leak_skill / ".env").write_text("SECRET=real_secret_val", encoding="utf-8")

        findings = run_security_scan(self.test_dir)
        rule_ids = [f["rule_id"] for f in findings]

        # 验证检测出 OpenAI Key 泄漏
        self.assertIn("SEC-001-OPENAI-KEY", rule_ids)
        # 验证检测出 Google Key 泄漏
        self.assertIn("SEC-002-GOOGLE-KEY", rule_ids)
        # 验证检测出 .env 文件
        self.assertIn("SEC-FILE-001", rule_ids)
        # 验证检测出 .key 文件
        self.assertIn("SEC-FILE-002", rule_ids)

        # 验证所有 findings 都包含了详细的建议修复指引 (remediation)
        for f in findings:
            self.assertTrue(len(f["remediation"]) > 0, f"Finding {f['rule_id']} 缺少 remediation 建议")
            # 验证代码片段脱敏
            if "OPENAI_KEY" in f.get("snippet", ""):
                self.assertNotIn("sk-1234567890abcdef1234567890abcdef", f["snippet"])

    def test_security_placeholder_filtering(self):
        """测试安全扫描对无害占位符的过滤."""
        safe_skill = self.test_dir / "safe-skill"
        safe_skill.mkdir(parents=True)
        (safe_skill / "SKILL.md").write_text(
            """---
name: safe-skill
description: 安全技能
version: 1.0.0
---
# Safe Skill
""",
            encoding="utf-8",
        )
        (safe_skill / "scripts").mkdir()
        (safe_skill / "scripts" / "demo.py").write_text(
            "API_KEY = 'your_openai_api_key_here'\n"
            "MOCK_KEY = 'example_key_00000000000000000000'\n",
            encoding="utf-8",
        )

        findings = run_security_scan(self.test_dir)
        self.assertEqual(len(findings), 0, f"示例占位符不应触发安全告警，但发现了: {findings}")

    def test_mask_secret(self):
        """测试脱敏掩码函数."""
        self.assertEqual(mask_secret("1234"), "****")
        masked = mask_secret("sk-1234567890abcdef12345678")
        self.assertTrue(masked.startswith("sk-1"))
        self.assertTrue(masked.endswith("5678"))
        self.assertIn("****", masked)


if __name__ == "__main__":
    unittest.main()
