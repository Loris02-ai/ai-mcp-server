import os
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware


ORIGIN_API = os.environ.get(
    "ORIGIN_API",
    "https://ai-check-system-production.up.railway.app"
)


app = FastAPI(
    title="Echoes MCP Server"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)



def get_activity():

    try:

        r = requests.get(
            f"{ORIGIN_API}/activity/summary",
            timeout=10
        )

        return r.json()

    except Exception as e:

        return {
            "error": str(e)
        }



@app.get("/")
def home():

    return {
        "status":"online",
        "server":"echoes-mcp"
    }



@app.post("/mcp")
async def mcp(
    request: Request
):

    body = await request.json()

    method = body.get(
        "method"
    )

    req_id = body.get(
        "id"
    )


    if method == "initialize":

        return {

            "jsonrpc":"2.0",

            "id":req_id,

            "result":{

                "protocolVersion":
                "2024-11-05",

                "capabilities":{

                    "tools":{}

                },

                "serverInfo":{

                    "name":
                    "echoes-mcp",

                    "version":
                    "1.0"

                }

            }

        }



    if method == "tools/list":

        return {

            "jsonrpc":"2.0",

            "id":req_id,

            "result":{

                "tools":[

                    {

                    "name":
                    "check_phone",

                    "description":
                    "查询手机活动记录",

                    "inputSchema":{

                        "type":"object",

                        "properties":{}

                    }

                    }

                ]

            }

        }



    if method == "tools/call":


        result = get_activity()


        return {

            "jsonrpc":"2.0",

            "id":req_id,

            "result":{

                "content":[

                    {

                    "type":"text",

                    "text":
                    str(result)

                    }

                ]

            }

        }



    return {

        "jsonrpc":"2.0",

        "id":req_id,

        "error":{

            "message":
            "unknown method"

        }

    }
