from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import json
import os
import uvicorn

from urllib.parse import quote
from urllib.request import Request as URLRequest
from urllib.request import urlopen


app = FastAPI(
    title="Echoes MCP Server"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# 环境变量
# =========================

ORIGIN_API = os.environ.get(
    "ORIGIN_API",
    ""
).rstrip("/")


BARK_API_KEY = os.environ.get(
    "BARK_API_KEY",
    ""
).strip()


# =========================
# HTTP 工具
# =========================

def get_json(url: str):

    req = URLRequest(
        url,
        headers={
            "User-Agent": "Echoes-MCP/1.0"
        }
    )

    with urlopen(
        req,
        timeout=10
    ) as response:

        text = response.read().decode(
            "utf-8"
        )

        return json.loads(text)


# =========================
# MCP 工具函数
# =========================

def check_on_wife(
    limit: int = 10
):

    if not ORIGIN_API:

        return (
            "查岗失败："
            "Railway MCP 尚未配置 ORIGIN_API"
        )


    try:

        data = get_json(
            f"{ORIGIN_API}/activity/summary"
        )

    except Exception as e:

        return (
            f"查岗失败：{e}"
        )


    recent_apps = data.get(
        "recent_apps",
        []
    )

    sessions = data.get(
        "sessions",
        {}
    )


    try:

        limit = int(limit)

    except Exception:

        limit = 10


    limit = max(
        1,
        min(
            limit,
            50
        )
    )


    recent_apps = recent_apps[
        :limit
    ]


    lines = []


    if recent_apps:

        lines.append(
            "最近打开："
            +
            "、".join(
                recent_apps
            )
        )

    else:

        lines.append(
            "暂无最近 App 记录"
        )


    if sessions:

        lines.append(
            "使用时长："
        )


        sorted_sessions = sorted(
            sessions.items(),
            key=lambda item: item[1],
            reverse=True
        )


        for app_name, seconds in sorted_sessions:

            try:

                seconds = int(
                    seconds
                )

            except Exception:

                continue


            hours, remainder = divmod(
                seconds,
                3600
            )

            minutes, secs = divmod(
                remainder,
                60
            )


            if hours > 0:

                duration = (
                    f"{hours}小时"
                    f"{minutes}分"
                    f"{secs}秒"
                )

            elif minutes > 0:

                duration = (
                    f"{minutes}分"
                    f"{secs}秒"
                )

            else:

                duration = (
                    f"{secs}秒"
                )


            lines.append(
                f"{app_name}：{duration}"
            )

    else:

        lines.append(
            "暂无可计算的使用时长"
        )


    return "\n".join(
        lines
    )


def bark_alert(
    title: str = "Robin",
    content: str = ""
):

    if not content:

        return "推送失败：内容不能为空"


    if not BARK_API_KEY:

        return (
            "推送失败："
            "Railway MCP 尚未配置 BARK_API_KEY"
        )


    safe_key = quote(
        BARK_API_KEY,
        safe=""
    )

    safe_title = quote(
        str(title),
        safe=""
    )

    safe_content = quote(
        str(content),
        safe=""
    )


    url = (
        "https://api.day.app/"
        f"{safe_key}/"
        f"{safe_title}/"
        f"{safe_content}"
    )


    try:

        req = URLRequest(
            url,
            headers={
                "User-Agent": "Echoes-MCP/1.0"
            }
        )


        with urlopen(
            req,
            timeout=10
        ) as response:

            status_code = (
                response.getcode()
            )


        if status_code == 200:

            return "推送成功"


        return (
            "推送失败："
            f"HTTP {status_code}"
        )


    except Exception as e:

        return (
            f"推送异常：{e}"
        )


# =========================
# MCP 工具定义
# =========================

TOOLS = [

    {
        "name": "check_on_wife",

        "description":
            "查询手机最近打开的 App 和已经计算好的使用时长",

        "inputSchema": {

            "type": "object",

            "properties": {

                "limit": {
                    "type": "integer",
                    "description":
                        "最多返回多少条最近 App 记录",
                    "default": 10
                }

            }

        }

    },

    {
        "name": "bark_alert",

        "description":
            "通过 Bark 给手机发送通知",

        "inputSchema": {

            "type": "object",

            "properties": {

                "title": {
                    "type": "string",
                    "description":
                        "通知标题",
                    "default": "Robin"
                },

                "content": {
                    "type": "string",
                    "description":
                        "通知正文"
                }

            },

            "required": [
                "content"
            ]

        }

    }

]


FUNCTIONS = {

    "check_on_wife":
        check_on_wife,

    "bark_alert":
        bark_alert,

}


# =========================
# Railway 状态接口
# =========================

@app.get("/")
def home():

    return {

        "status":
            "online",

        "server":
            "echoes-mcp",

        "tools": [
            "check_on_wife",
            "bark_alert"
        ]

    }


# =========================
# MCP JSON-RPC
# =========================

@app.post("/mcp")
async def mcp(
    request: Request
):

    body = await request.json()


    method = body.get(
        "method"
    )

    params = body.get(
        "params"
    ) or {}

    request_id = body.get(
        "id"
    )


    # ---------------------
    # initialize
    # ---------------------

    if method == "initialize":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {

                "protocolVersion":
                    "2024-11-05",

                "capabilities": {
                    "tools": {}
                },

                "serverInfo": {
                    "name":
                        "echoes-mcp",

                    "version":
                        "1.0"
                }

            }

        }


    # ---------------------
    # notifications
    # ---------------------

    if method == "notifications/initialized":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {}

        }


    # ---------------------
    # ping
    # ---------------------

    if method == "ping":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {}

        }


    # ---------------------
    # tools/list
    # ---------------------

    if method == "tools/list":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {

                "tools":
                    TOOLS

            }

        }


    # ---------------------
    # tools/call
    # ---------------------

    if method == "tools/call":

        name = params.get(
            "name"
        )

        args = params.get(
            "arguments"
        ) or {}


        if name not in FUNCTIONS:

            return {

                "jsonrpc":
                    "2.0",

                "id":
                    request_id,

                "error": {

                    "code":
                        -32601,

                    "message":
                        "Unknown tool"

                }

            }


        try:

            result = FUNCTIONS[
                name
            ](
                **args
            )

        except Exception as e:

            result = (
                f"工具调用失败：{e}"
            )


        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {

                "content": [

                    {
                        "type":
                            "text",

                        "text":
                            str(result)
                    }

                ]

            }

        }


    # ---------------------
    # 未知方法
    # ---------------------

    return {

        "jsonrpc":
            "2.0",

        "id":
            request_id,

        "error": {

            "code":
                -32601,

            "message":
                f"Unknown method: {method}"

        }

    }


# =========================
# Railway 启动
# =========================

if __name__ == "__main__":

    uvicorn.run(

        app,

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                8000
            )
        )

    )
