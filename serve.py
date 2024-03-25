"""Integration Agent"""

# Imports
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

# import firebase_admin
# from firebase_admin import credentials, firestore

# Env Variables
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
tavily_api_key = os.getenv("TAVILY_API_KEY")
access_token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
session_store = {}

# Firestore
# cred = credentials.Certificate('path/to/your/firebase-credentials.json')
# firebase_admin.initialize_app(cred)
# db = firestore.client()

# App
app = FastAPI(
    title="LangChain Server",
    version="1.0",
    description="A simple API server using LangChain's Runnable interfaces",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Loader Tool
def create_loader(docslink: str):
    loader = WebBaseLoader(docslink)
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter()
    documents = text_splitter.split_documents(docs)
    return documents


# Tools
def create_tools(documents):
    embeddings = OpenAIEmbeddings()
    vector = FAISS.from_documents(documents, embeddings)
    retriever = vector.as_retriever()
    retriever_tool = create_retriever_tool(
        retriever,
        "docs_retriever",
        "Search for information about integrating with a travel provider. For any questions about what code to suggest, you must use this tool!",
    )
    search = TavilySearchResults()
    tools = [retriever_tool, search]
    return tools


# Schema
class AgentInvokeRequest(BaseModel):
    input: str = ""
    session_id: str
    docslink: str
    repo: str
    chat_history: List[BaseMessage] = Field(
        ...,
        extra={"widget": {"type": "chat", "input": "location"}},
    )


# Agent Route
@app.post("/agent/invoke")
async def agent_invoke(request: AgentInvokeRequest):
    """Invoke the agent response"""
    session_id = request.session_id
    session_data = session_store.get(session_id, {"step": 1})
    documents = create_loader(request.docslink)
    tools = create_tools(documents)
    if session_data["step"] == 1:
        github_loader = GithubFileLoader(
            repo=request.repo,
            access_token=access_token,
            github_api_url="https://api.github.com",
            file_filter=lambda file_path: (
                file_path.endswith(".js")
                and not "config" in file_path
                and not file_path.endswith("layout.js")
            )
            or file_path == "package.json",
        )
        github_documents = github_loader.load()
        print(f"GitHub documents loaded: {len(github_documents)} documents")
        file_paths = [doc.metadata["path"] for doc in github_documents]
        file_list_str = "\n".join(file_paths)
        github_file_content = "\n".join([doc.page_content for doc in github_documents])
        step_1_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator. "
                    "Given the following list of repository files and the documentation link to an API, identify which files are most relevant for integrating the API. Consider synonyms like Stays and Hotels are matches, as is Flights and Air, you make the decision which files are most appropriate. "
                    f"\n\nRepository Files:\n{file_list_str}\n",
                ),
                (
                    "user",
                    "Based on this list of file names, and the docs provided to you in this link, tell me the list of file names which are most likely to be appropriate for integration with these docs. Just provide me with the file names, no other text before or after the file names, literally.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_1_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "docslink": request.docslink,
            "repo": request.repo,
            # "github_file_content": github_file_content,
            "file_list": file_list_str,
        }
        response = await agent_executor.ainvoke(context)
        agent_response = response.get("output", "No response generated.")
        print(f"Response from agent: {agent_response}")
        suggested_files = response.get("output", "No files suggested.")
        print(f"Suggested files: {suggested_files}")

        suggested_files_list = suggested_files.split("\n")
        session_store[session_id] = {
            "step": 2,
            "suggested_files": suggested_files_list,
            "github_file_content": github_file_content,
            "file_list": file_list_str,
            "docslink": request.docslink,
        }
        return {
            "step": 1,
            "message": "Files suggested for refactoring",
            "suggested_files": suggested_files,
        }

    elif session_data["step"] == 2:
        print("Entering Step 2: Refactoring suggested files...")
        suggested_files = session_data.get("suggested_files", [])
        github_file_content = session_data.get("github_file_content", "")
        print(f"github_file_content: {github_file_content}")
        file_list_str = session_data.get("file_list", "")
        docslink = session_data.get("docslink", "")
        file_contents = github_file_content.split("\n\n---\n\n")
        file_paths = file_list_str.split("\n")
        filtered_content = "\n\n---\n\n".join(
            content
            for file_path, content in zip(file_paths, file_contents)
            if file_path in suggested_files
        )

        step_2_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator. "
                    "Your task now is to review the contents of the suggested files and the documentation provided at the docslink. Based on your review, propose the functions that need to be added or modified in the suggested files to integrate with the API effectively. "
                    "Consider synonyms like Stays and Hotels are matches, as is Flights and Air, when determining the relevance of functions. ",
                    # f"\n\nSuggested Files and Their Contents:\n{filtered_content}\n"
                    # f"\nDocumentation Link: {docslink}\n",
                ),
                (
                    "user",
                    "Please review the suggested file contents and the documentation at the provided link. Then, propose the functions that need to be added to these files for effective integration with the API. Provide the function signatures and a brief description of their purpose.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "repo": request.repo,
            "github_file_content": filtered_content,
            "file_list": file_list_str,
            "docslink": docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_2_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        action_result = response.get("output", "No action performed.")
        print(f"Refactoring result: {action_result}")

        session_store[session_id] = {
            "step": 3,
            "suggested_files": suggested_files,
            "github_file_content": github_file_content,
            "filtered_content": filtered_content,
            "action_result": action_result,
            "docslink": docslink,
        }

        formatted_agent_response = format_response(action_result)
        return {
            "step": 2,
            "message": "Refactoring performed on suggested files",
            "output": formatted_agent_response,
        }

    elif session_data["step"] == 3:
        print("Entering Step 3: Creating or Updating UI Components...")
        suggested_files = session_data.get("suggested_files", [])
        action_result = session_data.get("action_result", "")
        docslink = session_data.get("docslink", "")
        filtered_content = session_data.get("github_file_content", "")

        step_3_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on UI development. "
                    "Your task now is to create or update UI components based on the integration requirements identified in the previous steps. "
                    "Consider the functions proposed for integration and ensure the UI components can support these functionalities effectively. ",
                    # f"\n\nSuggested Files and Their Contents:\n{filtered_content}\n"
                    # f"\nIntegration Actions from Step 2:\n{action_result}\n"
                    # f"\nDocumentation Link: {docslink}\n"
                ),
                (
                    "user",
                    "Based on the integration actions identified and the documentation, propose the UI components that need to be created or updated. Provide the code for these components and a brief description of their purpose.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "docslink": docslink,
            "github_file_content": filtered_content,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_3_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        ui_update_result = response.get("output", "No UI update action performed.")
        print(f"UI Update result: {ui_update_result}")

        session_store[session_id] = {
            "step": 4,  # Prepare for the next step
            "suggested_files": suggested_files,
            "action_result": action_result,  # Results from step 2
            "ui_update_result": ui_update_result,  # Results from step 3
            "docslink": docslink,
            # Ensure "github_file_content" is correctly named if used in step 4
            "filtered_content": filtered_content,  # This might be needed if you're filtering content for step 4
        }

        formatted_ui_response = format_response(ui_update_result)
        return {
            "step": 3,
            "message": "UI components created or updated",
            "output": formatted_ui_response,
        }

    elif session_data["step"] == 4:
        print("Entering Step 4: Generating Backend Endpoints...")
        suggested_files = session_data.get("suggested_files", [])
        action_result = session_data.get("action_result", "")
        ui_update_result = session_data.get("ui_update_result", "")
        docslink = session_data.get("docslink", "")

        step_4_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on backend development. "
                    "Your task now is to generate or update backend endpoints based on the integration requirements and UI components identified in the previous steps. "
                    "Consider the functionalities proposed for integration and ensure the backend endpoints can support these functionalities effectively. ",
                    # f"\n\nIntegration Actions from Step 2:\n{action_result}\n"
                    # f"\nUI Update Actions from Step 3:\n{ui_update_result}\n"
                    # f"\nDocumentation Link: {docslink}\n"
                ),
                (
                    "user",
                    "Based on the integration actions and UI update actions identified, propose the backend endpoints that need to be created or updated. Provide the endpoint paths, HTTP methods, and a brief description of their purpose.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "docslink": docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_4_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        backend_endpoint_result = response.get(
            "output", "No backend endpoint action performed."
        )
        print(f"Backend Endpoint result: {backend_endpoint_result}")

        # Update session_store for step 4 completion or further steps
        session_store[session_id] = {
            "step": 5,  # Prepare for the next step or completion
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "backend_endpoint_result": backend_endpoint_result,
            "docslink": docslink,
        }

        formatted_backend_response = format_response(backend_endpoint_result)
        return {
            "step": 4,
            "message": "Backend endpoints generated or updated",
            "output": formatted_backend_response,
        }

    elif session_data["step"] == 5:
        print("Entering Step 5: API Key section...")
        suggested_files = session_data.get("suggested_files", [])
        action_result = session_data.get("action_result", "")
        ui_update_result = session_data.get("ui_update_result", "")
        docslink = session_data.get("docslink", "")

        step_5_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on backend development. "
                    "Where do I find the API key for this provider, give? Give me some general guidance around getting the API key in place, in a list format. Maximum 100 words",
                    # f"\n\nIntegration Actions from Step 2:\n{action_result}\n"
                    # f"\nUI Update Actions from Step 3:\n{ui_update_result}\n"
                    # f"\nDocumentation Link: {docslink}\n"
                ),
                (
                    "user",
                    "Where do I find the API key for this provider, give? Give me some general guidance around getting the API key in place, in a list format. Maximum 100 words",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "docslink": docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_5_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        backend_apiKey_result = response.get(
            "output", "No backend endpoint action performed."
        )
        print(f"Backend Endpoint result: {backend_apiKey_result}")

        session_store[session_id] = {
            "step": 6,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            # "backend_endpoint_result": backend_endpoint_result,
            "docslink": docslink,
        }

        formatted_api_key_response = format_response(backend_apiKey_result)
        return {
            "step": 5,
            "message": "API Key info sent",
            "output": formatted_api_key_response,
        }

    elif session_data["step"] == 6:
        print("Entering Step 6: Creating Integration Tests...")
        suggested_files = session_data.get("suggested_files", [])
        action_result = session_data.get("action_result", "")
        ui_update_result = session_data.get("ui_update_result", "")
        backend_apiKey_result = session_data.get("backend_apiKey_result", "")
        docslink = session_data.get("docslink", "")

        step_6_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on quality assurance. "
                    "Your task now is to create integration tests for the API provider based on the integration requirements identified in the previous steps. "
                    "Consider the functionalities proposed for integration and ensure the tests cover these functionalities effectively. ",
                    # f"\n\nIntegration Actions from Step 2:\n{action_result}\n"
                    # f"\nUI Update Actions from Step 3:\n{ui_update_result}\n"
                    # f"\nBackend API Key Actions from Step 5:\n{backend_apiKey_result}\n"
                    # f"\nDocumentation Link: {docslink}\n"
                ),
                (
                    "user",
                    "Based on the integration actions identified, propose integration tests that need to be created. Provide the test cases and a brief description of their purpose.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "backend_apiKey_result": backend_apiKey_result,
            "docslink": docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_6_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        integration_tests_result = response.get(
            "output", "No integration tests action performed."
        )
        print(f"Integration Tests result: {integration_tests_result}")

        session_store[session_id] = {
            "step": 7,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "backend_apiKey_result": backend_apiKey_result,
            "integration_tests_result": integration_tests_result,
            "docslink": docslink,
        }

        formatted_integration_tests_response = format_response(integration_tests_result)
        return {
            "step": 6,
            "message": "Integration tests created",
            "output": formatted_integration_tests_response,
        }

    elif session_data["step"] == 7:
        print("Entering Step 7: Scanning Codebase for Impact Analysis...")
        suggested_files = session_data.get("suggested_files", [])
        action_result = session_data.get("action_result", "")
        ui_update_result = session_data.get("ui_update_result", "")
        backend_apiKey_result = session_data.get("backend_apiKey_result", "")
        integration_tests_result = session_data.get("integration_tests_result", "")
        docslink = session_data.get("docslink", "")

        step_7_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert Travel API Integrator focusing on impact analysis. "
                    "Your task now is to scan the codebase and highlight any areas of business or application logic that might be impacted by this integration. "
                    "Consider the integration requirements identified in the previous steps and ensure to list the potentially impacted areas in a list format. ",
                    # f"\n\nIntegration Actions from Step 2:\n{action_result}\n"
                    # f"\nUI Update Actions from Step 3:\n{ui_update_result}\n"
                    # f"\nBackend API Key Actions from Step 5:\n{backend_apiKey_result}\n"
                    # f"\nIntegration Tests from Step 6:\n{integration_tests_result}\n"
                    # f"\nDocumentation Link: {docslink}\n"
                ),
                (
                    "user",
                    "Based on the integration actions identified and the results from previous steps, scan the codebase and list any areas that might be impacted by this integration.",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        context = {
            "input": "",
            "chat_history": request.chat_history,
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "backend_apiKey_result": backend_apiKey_result,
            "integration_tests_result": integration_tests_result,
            "docslink": docslink,
        }
        agent = create_openai_functions_agent(
            llm=ChatOpenAI(model="gpt-3.5-turbo", temperature=0),
            tools=tools,
            prompt=step_7_prompt,
        )
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=False)
        response = await agent_executor.ainvoke(context)
        impact_analysis_result = response.get(
            "output", "No impact analysis action performed."
        )
        print(f"Impact Analysis result: {impact_analysis_result}")

        session_store[session_id] = {
            "step": 8,  # Prepare for the next step or completion
            "suggested_files": suggested_files,
            "action_result": action_result,
            "ui_update_result": ui_update_result,
            "backend_apiKey_result": backend_apiKey_result,
            "integration_tests_result": integration_tests_result,
            "impact_analysis_result": impact_analysis_result,
            "docslink": docslink,
        }

        formatted_impact_analysis_response = format_response(impact_analysis_result)
        return {
            "step": 7,
            "message": "Impact analysis completed",
            "output": formatted_impact_analysis_response,
        }

def format_response(action_result):
    parts = action_result.split("\n")
    formatted_response = ""
    code_block_open = False

    for part in parts:
        if part.endswith(".js"):
            if code_block_open:
                formatted_response += "</code></pre>"
            formatted_response += f"<h3>{part}</h3><pre><code>"
            code_block_open = True
        else:
            formatted_response += f"{part}\n"

    if code_block_open:
        formatted_response += "</code></pre>"

    return formatted_response


# Root Route
@app.get("/")
async def root():
    return {"message": "Hello World"}


# Server
if __name__ == "__main__":
    uvicorn.run(
        "serve:app", host="localhost", port=8000, log_level="debug", reload=True
    )
