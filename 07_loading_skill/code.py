#!/usr/bin/env python3
"""
LangGraph实现的Skill Loading系统
参考learn-claude-code s07的两层设计：
- Layer 1: SYSTEM prompt包含技能目录（名称+描述）
- Layer 2: Agent按需调用load_skill加载完整内容

LangGraph版本使用状态图来管理agent的工作流程
"""
import os
from pathlib import Path
from typing import TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
import yaml

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# 配置
WORKDIR = Path.cwd()
SKILLS_DIR = WORKDIR / "skills"

# 解析SKILL.md的YAML frontmatter
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()
# 再次更改
