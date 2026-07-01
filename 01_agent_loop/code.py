from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END,START
from langgraph.prebuilt import ToolNode
from langchain.tools import tool
from operator import add
import subprocess
from dotenv import load_dotenv
import platform
system = platform.system()
import os
load_dotenv()
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage,SystemMessage,HumanMessage
from langgraph.graph.message import add_messages
from langchain_core.prompts import ChatPromptTemplate
from pathlib import Path

cwd = Path(__file__).parent.parent.resolve()
llm = ChatOpenAI(
    base_url=os.getenv("url"), api_key=os.getenv("api_key"), model="deepseek-v4-flash"
)
prompt = ChatPromptTemplate.from_messages([
   ('system',f"You are a coding agent at {cwd} on {system}. Use bash to solve tasks. Act, don't explain."),
   ('human', '{input}')
]) 

@tool
def run_bash(command: str) -> str:
    """runs a bash command"""
    dangerous = [
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "> /dev/",
        "format",
        "diskpart",
        "del /f /s /q",
        "rd /s /q",
        "rmdir /s /q",
        "takeown",
        "icacls",
        "reg delete",
        "netsh advfirewall set allprofiles state off",
        "sc delete",
        "cipher /w",
    ]

    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


llm_with_tools = llm.bind_tools(tools=[run_bash])
class State(TypedDict):
    messages:Annotated[list[BaseMessage],add]

def call_model(state:State):
    messages = state["messages"]

    result = llm_with_tools.invoke(messages)
    return {'messages':[result]}

def should_continue(state:State):
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


workflow = StateGraph(State)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode([run_bash]))
workflow.add_edge(START, "agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", END: END}
)
workflow.add_edge("tools", "agent")

graph = workflow.compile()



if __name__ == "__main__":
    print("🤖 S01 Agent - LangGraph版本")
    print(f"📁 目录: {cwd}")
    print("💡 输入问题，回车发送。输入 q 退出。\n")
    
    while True:
        try:
            query = input("\033[36m>>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        
        result = graph.invoke({
            "messages": prompt.format_messages(input=query)
        })
        
        ai_message = result["messages"][-1]
        if hasattr(ai_message, "content") and ai_message.content:
            print(f"\n\033[32m{ai_message.content}\033[0m")
        print()

