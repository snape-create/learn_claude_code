from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from dotenv import load_dotenv
load_dotenv()
llm = ChatOpenAI()