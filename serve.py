"""Langchain Duffel Agent"""

from typing import List
import os
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import GithubFileLoader
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.pydantic_v1 import BaseModel, Field
from langchain_core.messages import BaseMessage
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# logging.basicConfig(level=logging.DEBUG)

# Load environment variables from .env file
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
tavily_api_key = os.getenv("TAVILY_API_KEY")
access_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")


# 1. Load Retriever
loader = WebBaseLoader("https://duffel.com/docs/guides/getting-started-with-flights")
docs = loader.load()
text_splitter = RecursiveCharacterTextSplitter()
documents = text_splitter.split_documents(docs)
embeddings = OpenAIEmbeddings()
vector = FAISS.from_documents(documents, embeddings)
retriever = vector.as_retriever()


# 2. Create Tools
retriever_tool = create_retriever_tool(
    retriever,
    "langsmith_search",
    "Search for information about integrating with Duffel. For any questions about what code to suggest, you must use this tool!",
)
search = TavilySearchResults()
tools = [retriever_tool, search]


# 3. Create Agent
prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert Travel API Integrator. Your mission is to integrate the user's files with the Duffel API based on the content of their repository. "
            "1. Review the indexed Duffel docs to understand the API. "
            "2. List out the repository files for me. "
            "3. Analyze the repository files to identify where and how the Duffel API can be integrated. "
            "4. Suggest specific files and code changes for Duffel API integration.",
        ),
        ("user", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ]
)
llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)


# 4. App definition
app = FastAPI(
    title="LangChain Server",
    version="1.0",
    description="A simple API server using LangChain's Runnable interfaces",
)

# Add CORSMiddleware to the application instance to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 5. Schema
class GithubInfo(BaseModel):
    # access_token: str
    repo: str


class AgentInvokeRequest(BaseModel):
    input: str = ""  # Set a default value to make it optional
    chat_history: List[BaseMessage] = Field(
        ...,
        extra={"widget": {"type": "chat", "input": "location"}},
    )
    github_info: GithubInfo


@app.post("/agent/invoke")
async def agent_invoke(request: AgentInvokeRequest):
    """Invoke the agent response"""
    print(f"Request body: {request.json()}")
    print(f"Received input: {request.input}")
    github_loader = GithubFileLoader(
        repo=request.github_info.repo,
        access_token=access_token,
        github_api_url="https://api.github.com",
        file_filter=lambda file_path: file_path.endswith(".txt")
        or file_path.endswith(".md")
        or file_path.endswith(".js")
        or file_path.endswith(".json"),
    )
    github_documents = github_loader.load()

    if github_documents:
        print("Files loaded from the repository:")
        for doc in github_documents:
            print(doc.metadata["path"])
    else:
        print("No documents were loaded from the repository.")

    github_file_content = "\n".join([doc.page_content for doc in github_documents])
    try:
        context = {
            "input": request.input,
            "chat_history": request.chat_history,
            "github_file_content": github_file_content,
        }
        response = await agent_executor.ainvoke(context)
        agent_response = response.get("output", "No response generated.")
        print(f"Response from agent: {agent_response}")
    except Exception as e:  # pylint: disable=broad-except
        exception_type = e.__class__.__name__
        print(f"Error processing request: {exception_type}: {e}")
        return {
            "output": f"An error occurred while processing your request: {exception_type}"
        }

    def format_response(agent_response):
        parts = agent_response.split("```")
        formatted_response = ""
        for i, part in enumerate(parts):
            if i % 2 == 0:
                formatted_response += f"<p>{part}</p>"
            else:
                escaped_code = part.replace("<", "&lt;").replace(">", "&gt;")
                formatted_response += f"<pre><code>{escaped_code}</code></pre>"
        return formatted_response

    formatted_agent_response = format_response(agent_response)
    return {"output": formatted_agent_response}


if __name__ == "__main__":
    uvicorn.run(
        "serve:app", host="localhost", port=8000, log_level="debug", reload=True
    )