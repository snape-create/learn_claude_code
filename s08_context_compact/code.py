"""
s05_todo_write/langgraph_code.py — 完整可运行版本

Graph topology:
  START ──> agent ──┬──> tools ──> update_round ──> agent (loop)
                    │
                    └──> END
"""
from typing import TypedDict
import locale
import ast
import json
import os
import subprocess
import platform
from pathlib import Path
from typing import TypedDict, Annotated, Optional, Literal
from operator import add
import yaml
from langchain_core.messages import (
    BaseMessage, AIMessage, HumanMessage, ToolMessage, SystemMessage, messages_from_dict, message_to_dict, messages_to_dict
)
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END, START
from dotenv import load_dotenv
import time
load_dotenv()

system_type = platform.system()
WORKDIR = Path(__file__).parent.parent.resolve()
checkpointer = MemorySaver()
llm = ChatOpenAI(
    base_url=os.getenv("url"),
    api_key=os.getenv("api_key"),
    model=os.getenv('model'),
)
CONTEXT_LIMIT = 50000
KEEP_RECENT = 3
PERSIST_THRESHOLD = 30000
SKILLS_DIR = WORKDIR / 'skills'
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
# 放在文件顶部，全局定义一次
if platform.system() == "Windows":
    CONSOLE_ENCODING = locale.getpreferredencoding(False)
else:
    CONSOLE_ENCODING = "utf-8"
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR} on {system_type}. "
    f"Use {system_type}-compatible shell commands. "
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)
# s07: Skill catalog scan (used by build_system below)
def _parse_frontmatter(text: str) -> tuple[dict, str]:
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
SKILL_REGISTRY: dict[str, dict] = {}
def _scan_skills():
    if not SKILLS_DIR.exists():
        return
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest = d / "SKILL.md"
        if manifest.exists():
            raw = manifest.read_text()
            meta, body = _parse_frontmatter(raw)
            name = meta.get("name", d.name)
            desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
            SKILL_REGISTRY[name] = {"name": name, "description": desc, "content": raw}
_scan_skills()
def list_skills() -> str:
    if not SKILL_REGISTRY:
        return "(no skills found)"
    return "\n".join(f"- **{s['name']}**: {s['description']}" for s in SKILL_REGISTRY.values())
def build_system() -> str:
    catalog = list_skills()
    return (
        f"You are a coding agent at {WORKDIR} on {system_type}. "
        f"Use {system_type}-compatible shell commands. "
        f"Skills available:\n{catalog}\n"
        "Use load_skill to get full details when needed."
        "Before starting any multi-step task, use todo_write to plan your steps. "
        "Update status as you go."
    )
SYSTEM = build_system()
@tool
def load_skill(name: str) -> str:
    """Load the full content of a skill by name."""
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Skill not found: {name}"
    return skill["content"]
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
            command, shell=True, cwd=str(WORKDIR),
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
        file_path = (WORKDIR / path).resolve()
        if not file_path.is_relative_to(WORKDIR):
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
        file_path = (WORKDIR / path).resolve()
        if not file_path.is_relative_to(WORKDIR):
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
        file_path = (WORKDIR / path).resolve()
        if not file_path.is_relative_to(WORKDIR):
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
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
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
TOOLS = [run_bash, run_read, run_write,
         run_edit, run_glob, todo_write, load_skill]
SUB_TOOLS = [run_bash, run_read, run_write, run_edit, run_glob]
SUB_TOOLS_MAP = {t.name: t for t in SUB_TOOLS}
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
        "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
        "message": "Path escapes workspace",
    },
    {
        "tools": ["run_bash"],
        "check": lambda args: any(
            kw in args.get("command", "")
            for kw in ["rm ", "> /etc/", "chmod 777"]
        ),
        "message": "Dangerous bash command",
    },
]
def check_permission(tool_name: str, args: dict) -> Optional[str]:
    """三层权限检查: deny list → rules(user confirm) → allow"""
    if tool_name == "run_bash":
        for pattern in DENY_LIST:
            if pattern in args.get("command", ""):
                return f"Blocked: '{pattern}' is on the deny list"
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
        return None
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
def context_inject_hook(query: str) -> None:
    print(f"\033[90m[HOOK] UserPromptSubmit:to solve tasks:{query} need to work in {WORKDIR}\033[0m")
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
register_hook('UserPromptSubmit', context_inject_hook)
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
TOOLS_MAP = {t.name: t for t in TOOLS}
llm_with_tools = llm.bind_tools(tools=TOOLS)
# ═══════════════════════════════════════════════
#  Graph Nodes
# ═══════════════════════════════════════════════
def agent(state: State):
    """调用 LLM。"""
    rounds = state.get("rounds_since_todo", 0)
    extra: list[BaseMessage] = []
    if rounds >= 3:
        extra = [HumanMessage(content="<reminder>Update your todos.</reminder>")]
        rounds = 0
        print("\033[33m[NAG] Injecting todo reminder\033[0m")
    messages_for_llm = [SystemMessage(content=SYSTEM)] + state["messages"] + extra
    result = llm_with_tools.invoke(messages_for_llm)
    return {"messages": extra + [result], "rounds_since_todo": rounds}
def execute_tools(state: State):
    """权限检查 + 工具执行 + 计数器更新"""
    last_msg = state["messages"][-1]
    if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
        return {"messages": [], "rounds_since_todo": state.get("rounds_since_todo", 0) + 1}
    results: list[ToolMessage] = []
    todo_called = False
    for tc in last_msg.tool_calls:
        name, args, tc_id = tc["name"], tc["args"], tc["id"]
        if name == "todo_write":
            todo_called = True
        reason = check_permission(name, args)
        if reason:
            print(f"\033[31m⛔ {name}: {reason}\033[0m")
            results.append(ToolMessage(
                content=f"Permission denied: {reason}",
                tool_call_id=tc_id, name=name,
            ))
            continue
        trigger_hooks("PreToolUse", tc)
        tool_fn = TOOLS_MAP.get(name)
        output = tool_fn.invoke(args) if tool_fn else f"Unknown tool: {name}"
        msg = ToolMessage(content=str(output), tool_call_id=tc_id, name=name)
        trigger_hooks("PostToolUse", msg)
        results.append(msg)
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
workflow.add_edge('tools', 'agent')
graph = workflow.compile(checkpointer=checkpointer)
# ═══════════════════════════════════════════════
#  L3: toolResultBudget — persist large results to disk
# ═══════════════════════════════════════════════
def persist_large_output(tool_use_id, output):
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    if not path.exists():
        path.write_text(output)
    return f"<persisted-output>\nFull output: {path}\nPreview:\n{output[:2000]}\n</persisted-output>"
def tool_result_budget(messages: list[BaseMessage], max_bytes=200_000, persist_threshold=30000):
    """适配LangChain格式：检查最近连续的ToolMessage"""
    if not messages or not isinstance(messages[-1], ToolMessage):
        return messages
    tool_msgs = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            tool_msgs.insert(0, msg)
        else:
            break
    if not tool_msgs:
        return messages
    total = sum(len(str(msg.content)) for msg in tool_msgs)
    if total <= max_bytes:
        return messages
    ranked = sorted(tool_msgs, key=lambda m: len(str(m.content)), reverse=True)
    for block in ranked:
        if total <= max_bytes:
            break
        content = str(getattr(block, 'content', ''))
        if len(content) <= persist_threshold:
            continue
        tid = getattr(block, 'tool_call_id', str(time.time()))
        block.content = persist_large_output(tid, content)
        total = sum(len(str(getattr(r, 'content', ''))) for r in tool_msgs)
    return messages
# ═══════════════════════════════════════════════
#  Helper functions for compaction
# ═══════════════════════════════════════════════
def _message_has_tool_use(message: BaseMessage):
    if not isinstance(message, AIMessage):
        return False
    tool_calls = getattr(message, 'tool_calls', None)
    if not tool_calls:
        return False
    return True
def _is_tool_result_message(message: BaseMessage):
    if not isinstance(message, ToolMessage):
        return False
    return True
# ═══════════════════════════════════════════════
#  L1: snipCompact — trim middle messages
# ═══════════════════════════════════════════════
def snip_compact(messages, max_messages=100):
    if len(messages) <= max_messages:
        return messages
    keep_head, keep_tail = 3, max_messages - 3
    head_end, tail_start = keep_head, len(messages) - keep_tail
    if head_end > 0 and _message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and _is_tool_result_message(messages[head_end]):
            head_end += 1
    if (tail_start > 0 and tail_start < len(messages)
            and _is_tool_result_message(messages[tail_start])
            and _message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    snipped = tail_start - head_end
    return messages[:head_end] + [SystemMessage(content=f"[snipped {snipped} messages]")] + messages[tail_start:]
# ═══════════════════════════════════════════════
#  L2: microCompact — old result placeholders
# ═══════════════════════════════════════════════
def collect_tool_results(messages: list[BaseMessage]):
    """收集所有ToolMessage的引用"""
    blocks = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            continue
        blocks.append(msg)
    return blocks
def micro_compact(messages: list[BaseMessage]):
    """替换旧的ToolMessage内容为占位符"""
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= KEEP_RECENT:
        return messages
    for tr in tool_results[:-KEEP_RECENT]:
        content = str(getattr(tr, 'content', ''))
        if len(content) > 120:
            idx = messages.index(tr)
            messages[idx] = ToolMessage(
                content="[Earlier tool result compacted. Re-run if needed.]",
                tool_call_id=tr.tool_call_id,
                name=tr.name
            )
    return messages
# ═══════════════════════════════════════════════
#  L4: autoCompact — LLM full summary
# ═══════════════════════════════════════════════
def estimate_size(msgs):
    return len(str(msgs))
def write_transcript(messages: list[BaseMessage]):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w") as f:
        for msg in messages:
            f.write(json.dumps(message_to_dict(msg), default=str) + "\n")
    return path
def summarize_history(messages: list[BaseMessage]):
    conversation = json.dumps(messages_to_dict(messages), default=str)[:80000]
    prompt = ("Summarize this coding-agent conversation so work can continue.\n"
              "Preserve: 1. current goal, 2. key findings/decisions, 3. files read/changed, "
              "4. remaining work, 5. user constraints.\nBe compact but concrete.\n\n" + conversation)
    try:
        response = llm.invoke(prompt)
    except Exception as e:
        response = None
    return getattr(response, 'content', '(empty summary)') if response else 'llm summarize error'
def compact_history(messages: list[BaseMessage], user_query: str = ""):
    """压缩对话历史，使用SystemMessage存储摘要，保留用户最后的问题"""
    transcript_path = write_transcript(messages)
    print(f"[transcript saved: {transcript_path}]")
    summary = summarize_history(messages)
    result = [SystemMessage(content=f'[Compacted]\n\n{summary}')]
    # 保留用户最后的问题
    if user_query:
        result.append(HumanMessage(content=user_query))
    return result
# ═══════════════════════════════════════════════
#  Emergency: reactive_compact — on API error
# ═══════════════════════════════════════════════
def reactive_compact(messages: list[BaseMessage], user_query: str = ""):
    """应急压缩，使用SystemMessage存储摘要，保留用户最后的问题"""
    transcript = write_transcript(messages)
    msg_count = len(messages)
    tail_start = max(0, msg_count - 5)
    if (tail_start > 0 and tail_start < msg_count
            and _is_tool_result_message(messages[tail_start])
            and _message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    summary = summarize_history(messages[:tail_start])
    result = [SystemMessage(content=f"[Reactive compact]\n\n{summary}")] + messages[tail_start:]
    # 保留用户最后的问题
    if user_query:
        result.append(HumanMessage(content=user_query))
    return result
# ═══════════════════════════════════════════════
#  agent_loop — 主循环
# ═══════════════════════════════════════════════
def agent_loop(history: list[BaseMessage], config: dict = None):
    """主循环：压缩 + 调用 graph"""
    # 保留用户最后的问题
    user_query = ""
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            user_query = msg.content
            break
    # 上下文压缩
    history[:] = tool_result_budget(history)
    history[:] = snip_compact(history)
    history[:] = micro_compact(history)
    if estimate_size(history) >= CONTEXT_LIMIT:
        print("[auto compact]")
        history[:] = compact_history(history, user_query)
    reactive_retries = 0
    MAX_REACTIVE_RETRIES = 1
    while True:
        try:
            result = graph.invoke({"messages": history}, config=config)
            return result["messages"]
        except Exception as e:
            if ("prompt_too_long" in str(e).lower() or "too many tokens" in str(e).lower()) and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[reactive compact]")
                history[:] = reactive_compact(history, user_query)
                reactive_retries += 1
            else:
                raise
# ═══════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    print("s08: Context Compact — LangGraph version")
    print(f"📁 Working in: {WORKDIR}")
    print("Type a question, press Enter. Type q to quit.\n")
    config = {"configurable": {"thread_id": "user_001"}}
    history = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        # 添加用户消息到历史
        history.append(HumanMessage(content=query))
        # 使用 agent_loop（包含压缩逻辑）
        history = agent_loop(history, config)
        ai = history[-1]
        if isinstance(ai, AIMessage) and ai.content:
            print(f"\n\033[32m{ai.content}\033[0m")
        print()
