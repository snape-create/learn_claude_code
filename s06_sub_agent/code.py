"""
s05_todo_write/langgraph_code.py — 完整可运行版本

Graph topology:
  START ──> agent ──┬──> tools ──> update_round ──> agent (loop)
                    │
                    └──> END
"""

import ast
import json
import os
import subprocess
import platform
from pathlib import Path
from typing import TypedDict, Annotated, Optional,Literal
from operator import add

from langchain_core.messages import (
    BaseMessage, AIMessage, HumanMessage, ToolMessage, SystemMessage,
)
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END, START
from dotenv import load_dotenv

load_dotenv()


system_type = platform.system()
cwd = Path(__file__).parent.parent.resolve()
checkpointer = MemorySaver()

llm = ChatOpenAI(
    base_url=os.getenv("url"),
    api_key=os.getenv("api_key"),
    model="deepseek-v4-pro",
)
import locale

# 放在文件顶部，全局定义一次
if platform.system() == "Windows":
    # Windows 中文系统: cmd/PowerShell 默认输出 GBK
    # PowerShell 有时输出 UTF-8，但 cmd.exe 是 GBK
    CONSOLE_ENCODING = locale.getpreferredencoding(False)  # 通常是 'cp936'
else:
    CONSOLE_ENCODING = "utf-8"
SYSTEM = (
    f"You are a coding agent at {cwd} on {system_type}. "
    f"Use {system_type}-compatible shell commands. " 
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go."
)
SUB_SYSTEM = (
    f"You are a coding agent at {cwd} on {system_type}. "
    f"Use {system_type}-compatible shell commands. " 
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add]
    rounds_since_todo: int         



@tool
def run_bash(command: str) -> str:
    """Run a bash command. Prefer file-specific tools for file operations."""
    dangerous = [
        "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/",
        "format", "diskpart", "del /f /s /q", "rd /s /q",
    ]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command, shell=True, cwd=str(cwd),
            capture_output=True,
            timeout=120,
            encoding=CONSOLE_ENCODING,   
            errors="replace",            
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except Exception as e:
        return f"Error: {e}"
@tool
def run_read(path: str, limit: Optional[int] = None) -> str:
    """Read file contents."""
    try:
        file_path = (cwd / path).resolve()
        if not file_path.is_relative_to(cwd):
            return "Error: Path escapes workspace"
        lines = file_path.read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@tool
def run_write(path: str, content: str) -> str:
    """Write content to a file."""
    try:
        file_path = (cwd / path).resolve()
        if not file_path.is_relative_to(cwd):
            return "Error: Path escapes workspace"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


@tool
def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in a file once."""
    try:
        file_path = (cwd / path).resolve()
        if not file_path.is_relative_to(cwd):
            return "Error: Path escapes workspace"
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


@tool
def run_glob(pattern: str) -> str:
    """Find files matching a glob pattern."""
    import glob as g
    try:
        results = []
        for match in g.glob(pattern, root_dir=cwd):
            if (cwd / match).resolve().is_relative_to(cwd):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


CURRENT_TODOS: list[dict] = []


def _normalize_todos(todos):
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            try:
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"
    if not isinstance(todos, list):
        return None, "Error: todos must be a list"
    for i, t in enumerate(todos):
        if not isinstance(t, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in t or "status" not in t:
            return None, f"Error: todos[{i}] missing 'content' or 'status'"
        if t["status"] not in ("pending", "in_progress", "completed"):
            return None, f"Error: todos[{i}] has invalid status '{t['status']}'"
    return todos, None

from typing import TypedDict

class TodoItem(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]

@tool
def todo_write(todos: list[TodoItem]) -> str:
    """Create and manage a task list for your current coding session."""
    global CURRENT_TODOS
    todos, err = _normalize_todos(todos)
    if err:
        return err                  
    CURRENT_TODOS = todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for t in CURRENT_TODOS:
        icon = {
            "pending": " ",
            "in_progress": "\033[36m▸\033[0m",
            "completed": "\033[32m✓\033[0m",
        }[t["status"]]
        lines.append(f"  [{icon}] {t['content']}")
    print("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"


TOOLS = [run_bash, run_read, run_write, run_edit, run_glob, todo_write]


SUB_TOOLS= [run_bash, run_read, run_write, run_edit, run_glob]
SUB_TOOLS_MAP = {t.name:t for t in SUB_TOOLS}
# ═══════════════════════════════════════════════
#  Permission System — 修正: 工具名统一为 run_bash
# ═══════════════════════════════════════════════

DENY_LIST = [
    "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/",
    "format", "diskpart", "del /f /s /q", "rd /s /q",
]

PERMISSION_RULES = [
    {
        "tools": ["run_write", "run_read", "run_edit"],
        "check": lambda args: not (cwd / args.get("path", "")).resolve().is_relative_to(cwd),
        "message": "Path escapes workspace",
    },
    {
        "tools": ["run_bash"],                       # ← 修正: 之前写的是 'bash'
        "check": lambda args: any(
            kw in args.get("command", "")
            for kw in ["rm ", "> /etc/", "chmod 777"]
        ),
        "message": "Dangerous bash command",
    },
]


def check_permission(tool_name: str, args: dict) -> Optional[str]:
    """三层权限检查: deny list → rules(user confirm) → allow"""
    # Layer 1: hard deny
    if tool_name == "run_bash":                       # ← 修正: 之前写的是 'bash'
        for pattern in DENY_LIST:
            if pattern in args.get("command", ""):
                return f"Blocked: '{pattern}' is on the deny list"

    # Layer 2: rules → ask user
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return ask_user(tool_name, args, rule["message"])

    return None


def ask_user(tool_name: str, args: dict, reason: str) -> Optional[str]:
    """交互式确认 — 返回 None (allow) 或 拒绝原因字符串"""
    print(f"\n\033[33m⚠  {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    if choice in ("y", "yes"):
        return None                     # None = 放行
    return f"User denied: {reason}"

# ═══════════════════════════════════════════════
#  Hooks (与 s04 一致)
# ═══════════════════════════════════════════════

HOOKS = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    for cb in HOOKS[event]:
        result = cb(*args)
        if result is not None:
            return result
    return None

def context_inject_hook(query:str)-> None:
    """记录工作目录"""
    print(f"\033[90m[HOOK] UserPromptSubmit:to solve tasks:{query} need to work in {cwd}\033[0m")
    return None 

def log_hook(tool_call):
    print(f"\033[90m[HOOK] PreToolUse: {tool_call.get('name', '?')}\033[0m")


def large_output_hook(tool_message):
    content = getattr(tool_message, "content", "") or ""
    if len(str(content)) > 100000:
        name = getattr(tool_message, "name", "?")
        print(f"\033[33m[HOOK] ⚠ Large output from {name}: {len(str(content))} chars\033[0m")


def summary_hook(messages: list):
    tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")

register_hook('UserPromptSubmit',context_inject_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)

sub_llm = llm.bind_tools(tools=SUB_TOOLS)



def extract_text(message) -> str:
    """从 AIMessage中提取纯文本。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


@tool
def spawn_subagent(description: str) -> str:
    """Launch a subagent to handle a complex subtask. Returns only the final conclusion."""
    print(f"\n\033[35m[Subagent spawned]\033[0m")
    messages = [HumanMessage(content=description)]

    for _ in range(30):
        response = sub_llm.invoke(
            [SystemMessage(content=SUB_SYSTEM)] + messages
        )
        messages.append(response)
        if not getattr(response, 'tool_calls', None):
            break
        results = []
        for tc in response.tool_calls:
            name, args, tc_id = tc["name"], tc["args"], tc["id"]

            reason = check_permission(name, args)
            if reason:
                print(f"\033[31m⛔ [sub] {name}: {reason}\033[0m")
                results.append(ToolMessage(
                    content=f"Permission denied: {reason}",
                    tool_call_id=tc_id, name=name,
                ))
                continue

            handler = SUB_TOOLS_MAP.get(name)
            output = handler.invoke(args) if handler else f"Unknown tool: {name}"
            msg = ToolMessage(content=str(output), tool_call_id=tc_id, name=name)
            trigger_hooks("PostToolUse", msg)
            print(f"\033[90m[sub] {name}: {str(output)[:100]}\033[0m")
            results.append(msg)

        messages.extend(results)

    # 提取最终文本摘要
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            result = extract_text(msg)
            if result:
                break
    else:
        result = "Subagent stopped after 30 turns without final answer."

    print(f"\033[35m[Subagent done]\033[0m")
    return result 

TOOLS.append(spawn_subagent)
TOOLS_MAP = {t.name:t for t in TOOLS}

llm_with_tools = llm.bind_tools(tools=TOOLS)
# ═══════════════════════════════════════════════
#  Graph Nodes
# ═══════════════════════════════════════════════

def agent(state: State):
    """
    调用 LLM。
    - SystemMessage 在此注入，不存入 state（避免重复累积）
    - Nag reminder 作为 HumanMessage 注入并存入 state
    """
    rounds = state.get("rounds_since_todo", 0)

    # ── Nag check ──
    extra: list[BaseMessage] = []
    if rounds >= 3:
        extra = [HumanMessage(content="<reminder>Update your todos.</reminder>")]
        rounds = 0
        print("\033[33m[NAG] Injecting todo reminder\033[0m")

    # 构建 LLM 输入: SystemMessage 始终在最前面，但不写入 state
    messages_for_llm = [SystemMessage(content=SYSTEM)] + state["messages"] + extra
    result = llm_with_tools.invoke(messages_for_llm)

    # 只把 extra(reminder) + result(assistant) 写入 state
    return {"messages": extra + [result], "rounds_since_todo": rounds}


def execute_tools(state: State):
    """
    权限检查 + 工具执行 + 计数器更新，一步完成。
    不需要单独的 update_round 节点。
    """
    last_msg = state["messages"][-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {"messages": [], "rounds_since_todo": state.get("rounds_since_todo", 0) + 1}

    results: list[ToolMessage] = []
    todo_called = False

    for tc in last_msg.tool_calls:
        name, args, tc_id = tc["name"], tc["args"], tc["id"]

        # ── 检测 todo_write 调用 ──
        if name == "todo_write":
            todo_called = True

        # ── 权限检查 ──
        reason = check_permission(name, args)
        if reason:
            print(f"\033[31m⛔ {name}: {reason}\033[0m")
            results.append(ToolMessage(
                content=f"Permission denied: {reason}",
                tool_call_id=tc_id, name=name,
            ))
            continue

        # ── 执行 ──
        trigger_hooks("PreToolUse", tc)
        tool_fn = TOOLS_MAP.get(name)
        output = tool_fn.invoke(args) if tool_fn else f"Unknown tool: {name}"
        msg = ToolMessage(content=str(output), tool_call_id=tc_id, name=name)
        trigger_hooks("PostToolUse", msg)
        results.append(msg)

    # ── 计数器: 在执行工具时就确定，不需要额外节点 ──
    new_rounds = 0 if todo_called else state.get("rounds_since_todo", 0) + 1

    return {"messages": results, "rounds_since_todo": new_rounds}





# ═══════════════════════════════════════════════
#  Routing
# ═══════════════════════════════════════════════

def should_continue(state: State) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    trigger_hooks("Stop", state["messages"])
    return END

# ═══════════════════════════════════════════════
#  Build Graph
# ═══════════════════════════════════════════════

workflow = StateGraph(State)

workflow.add_node("agent", agent)
workflow.add_node("tools", execute_tools)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    END: END,
})
workflow.add_edge('tools','agent')

graph = workflow.compile(checkpointer=checkpointer)



if __name__ == "__main__":
    print("s05: TodoWrite — LangGraph version")
    print(f"📁 Working in: {cwd}")
    print("Type a question, press Enter. Type q to quit.\n")

    config = {"configurable": {"thread_id": "user_001"}}

    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        trigger_hooks("UserPromptSubmit", query)

        # 只传 HumanMessage，SystemMessage 由 agent 节点注入
        result = graph.invoke(
            {"messages": [HumanMessage(content=query)]},
            config=config,
        )

        ai = result["messages"][-1]
        if isinstance(ai, AIMessage) and ai.content:
            print(f"\n\033[32m{ai.content}\033[0m")
        print()