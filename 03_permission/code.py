from langchain_core.messages import BaseMessage, AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langgraph.checkpoint.memory import MemorySaver
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from typing import TypedDict, Annotated
import os
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langchain.tools import tool
from operator import add
import subprocess
from typing import Optional, Literal
from dotenv import load_dotenv
import platform
system = platform.system()
load_dotenv()
checkpointer = MemorySaver()
cwd = Path(__file__).parent.parent.resolve()
llm = ChatOpenAI(
    base_url=os.getenv("url"), api_key=os.getenv("api_key"), model="deepseek-chat"
)
prompt = ChatPromptTemplate.from_messages([
    ('system',
     f"You are a coding agent at {cwd} on {system}. Use bash to solve tasks. Act, don't explain."),
    ('human', '{input}')
])


@tool
def run_bash(command: str) -> str:
    """ONLY use this as a last resort when other tools cannot accomplish the task.
    Prefer run_read, run_write, run_edit, run_glob for file operations.
    Runs a bash command."""
    dangerous = [
        "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/",
        "format", "diskpart", "del /f /s /q", "rd /s /q",
        "rmdir /s /q", "takeown", "icacls", "reg delete",
        "netsh advfirewall set allprofiles state off",
        "sc delete", "cipher /w",
    ]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        import locale
        encoding = locale.getpreferredencoding()

        r = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=False,
            timeout=120,
        )

        stdout = r.stdout.decode(
            encoding, errors="replace") if r.stdout else ""
        stderr = r.stderr.decode(
            encoding, errors="replace") if r.stderr else ""

        out = (stdout + stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"




@tool
def run_read(path: str, limit: Optional[int]):
    """ read file
    Args:
        path (str): file path
        limit (Optional[int]): limit the content of file

    Returns:
        _type_: the content of file
    """
    try:
        file_path = Path(path)
        content = file_path.read_text().splitlines()
        if limit and len(content) > limit:
            content = content[:limit] + \
                [f"... ({len(content) - limit} more lines)"]
        return "\n".join(content)
    except Exception as e:
        return f"Error: {e}"


@tool
def run_write(path: str, content: str) -> str:
    """write content to path

    Args:
        path (str): _description_
        content (str): _description_

    Returns:
        str: _description_
    """
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f'error:{e}'


@tool
def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in a file once.

    Args:
        path (str): _description_
        old_text (str): _description_
        new_text (str): _description_

    Returns:
        str: _description_
    """
    try:
        file_path = Path(path)
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


@tool
def run_glob(pattern: str) -> str:
    """Find files matching a glob pattern.

    Args:
        pattern (str): _description_

    Returns:
        str: _description_
    """
    import glob as g
    try:
        results = []
        for match in g.glob(pattern, root_dir=cwd):
            if (cwd / match).resolve().is_relative_to(cwd):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


DENY_LIST = [
    "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/",
    "format", "diskpart", "del /f /s /q", "rd /s /q",
    "rmdir /s /q", "takeown", "icacls", "reg delete",
    "netsh advfirewall set allprofiles state off",
    "sc delete", "cipher /w",
]


def check_deny_list(command: str) -> Optional[str]:
    """检查命令是否包含危险命令"""
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None


PERMISSION_RULES = [
    {'tools': ['run_write','run_read','run_edit'],
     'check': lambda args: not (cwd / args.get('path', '')).resolve().is_relative_to(cwd),
     'message': "Writing outside workspace"},
    {'tools': ['bash'],
     'check': lambda args: any(kw in args.get("command", "") for kw in ["rm ", "> /etc/", "chmod 777"]),
     'message': "Bash commands are not allowed"},
]


def check_rules(tool_name: str, args: dict) -> Optional[str]:
    """风险监测提示"""
    for rule in PERMISSION_RULES:
        # 判断特定工具name和参数是否符合权限,不符合权限返回提示信息
        if tool_name in rule['tools'] and rule['check'](args):
            return rule['message']
    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    """返回allow 或者 deny """
    print(f"\n\033[33m⚠  {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"


TOOLS = [ run_read, run_write, run_glob, run_edit]
llm_with_tools = llm.bind_tools(tools=TOOLS)


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add]


def call_model(state: State):
    messages = state["messages"]
    result = llm_with_tools.invoke(messages)
    return {'messages': [result]}


def check_permission(tool_name: str, args: dict) -> Optional[str]:
    """
    三道闸门进行检验
    """
    if tool_name == 'bash':
        reason = check_deny_list(args.get('command', ''))
        # 包含危险命令
        if reason:
            print(f"\n\033[31m⛔ {reason}\033[0m")
            return reason
    reason = check_rules(tool_name, args)
    if reason:
        if ask_user(tool_name, args, reason) == "deny":
            return f"User denied: {reason}"
    return None


class PermissionToolNode(ToolNode):
    def _run_one(self, tool_call: dict, input_type: str, config) -> ToolMessage:
        name = tool_call.get('name', '')
        args = tool_call.get('args', {}) or {}
        tool_call_id = tool_call.get('id', '')

        print(f"\033[36m> {name}\033[0m")

        reason = check_permission(name, args)
        if reason:
            print(f"\033[31m⛔ {name}: {reason}\033[0m")
            return ToolMessage(
                content=f"Permission denied: {reason}",
                tool_call_id=tool_call_id,
            )

        result = super()._run_one(tool_call, input_type, config)
        print(f"\033[90m[DEBUG] {name} result: {result.content[:100] if result.content else 'EMPTY'}\033[0m")
        return result





def should_continue(state: State):
    last_message: AIMessage = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return 'tools'
    return END


workflow = StateGraph(State)
workflow.add_node("agent", call_model)
workflow.add_node('tools', PermissionToolNode(TOOLS))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {'tools': "tools", END: END}
)
workflow.add_edge('tools', 'agent')

graph = workflow.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    print("🤖 S01 Agent - LangGraph版本")
    print(f"📁 目录: {cwd}")
    print("💡 输入问题，回车发送。输入 q 退出。\n")
    config = {"configurable": {"thread_id": "user_001"}}
    while True:
        try:
            query = input("\033[36m>>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        result = graph.invoke({
            "messages": prompt.format_messages(input=query)
        }, config=config)

        ai_message = result["messages"][-1]

        if hasattr(ai_message, "content") and ai_message.content:
            print(f"\n\033[32m{ai_message.content}\033[0m")
        print()
