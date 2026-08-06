from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn


app = FastAPI(
    title="Echoes MCP Server"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


TOOLS = [
    {
        "name": "get_status",
        "description": "获取 Echoes MCP 服务状态",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


def get_status():
    return "Echoes MCP Server is online"


FUNCTIONS = {
    "get_status": get_status
}


@app.get("/")
def home():
    return {
        "status": "online",
        "server": "echoes-mcp"
    }


@app.post("/mcp")
async def mcp(request: Request):

    body = await request.json()

    method = body.get("method")
    params = body.get("params") or {}
    request_id = body.get("id")


    if method == "initialize":

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "echoes-mcp",
                    "version": "1.0"
                }
            }
        }


    if method == "tools/list":

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": TOOLS
            }
        }


    if method == "tools/call":

        name = params.get("name")

        args = params.get("arguments") or {}


        if name not in FUNCTIONS:

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Unknown tool"
                }
            }


        result = FUNCTIONS[name](**args)


        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": result
                    }
                ]
            }
        }


    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": "Unknown method"
        }
    }



if __name__ == "__main__":

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT",8000))
    )
