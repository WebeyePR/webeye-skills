"""
Configuration for Example Skill
"""

import os

# 基础配置
DEFAULT_MAX_LENGTH = int(os.getenv("SUMMARIZER_MAX_LENGTH", 100))

# API 配置（占位）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
