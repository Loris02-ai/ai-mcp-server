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


AUTH_TOKEN = os.environ.get(
    "AUTH_TOKEN",
    ""
).strip()


# =========================
# HTTP 工具
# =========================

def get_json(
    url,
    use_auth=False
):

    headers = {
        "User-Agent":
            "Echoes-MCP/1.0"
    }


    if use_auth:

        headers[
            "Authorization"
        ] = (
            f"Bearer {AUTH_TOKEN}"
        )


    req = URLRequest(
        url,
        headers=headers
    )


    with urlopen(
        req,
        timeout=10
    ) as response:

        text = (
            response
            .read()
            .decode(
                "utf-8"
            )
        )

        return json.loads(
            text
        )


def post_json(
    url,
    data=None
):

    if data is None:

        data = {}


    payload = json.dumps(
        data,
        ensure_ascii=False
    ).encode(
        "utf-8"
    )


    req = URLRequest(
        url,
        data=payload,
        headers={
            "Authorization":
                f"Bearer {AUTH_TOKEN}",

            "Content-Type":
                "application/json",

            "User-Agent":
                "Echoes-MCP/1.0"
        },
        method="POST"
    )


    with urlopen(
        req,
        timeout=10
    ) as response:

        text = (
            response
            .read()
            .decode(
                "utf-8"
            )
        )

        return json.loads(
            text
        )


# =========================
# 查岗
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

        limit = int(
            limit
        )

    except Exception:

        limit = 10


    limit = max(
        1,
        min(
            limit,
            50
        )
    )


    recent_apps = (
        recent_apps[
            :limit
        ]
    )


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
            key=lambda item:
                item[1],
            reverse=True
        )


        for (
            app_name,
            seconds
        ) in sorted_sessions:

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
                f"{app_name}："
                f"{duration}"
            )

    else:

        lines.append(
            "暂无可计算的使用时长"
        )


    return "\n".join(
        lines
    )


# =========================
# Bark
# =========================

def bark_alert(
    title: str = "Robin",
    content: str = ""
):

    if not content:

        return (
            "推送失败：内容不能为空"
        )


    if not BARK_API_KEY:

        return (
            "推送失败："
            "Railway MCP 尚未配置 "
            "BARK_API_KEY"
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
                "User-Agent":
                    "Echoes-MCP/1.0"
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
# 宵禁：暂停指定时间
# =========================

def curfew_pause(
    minutes: int = 60
):

    if not ORIGIN_API:

        return (
            "暂停失败："
            "未配置 ORIGIN_API"
        )


    if not AUTH_TOKEN:

        return (
            "暂停失败："
            "未配置 AUTH_TOKEN"
        )


    try:

        minutes = int(
            minutes
        )

    except Exception:

        minutes = 60


    minutes = max(
        1,
        min(
            minutes,
            720
        )
    )


    try:

        data = post_json(
            f"{ORIGIN_API}/curfew/pause",
            {
                "minutes":
                    minutes
            }
        )

    except Exception as e:

        return (
            f"暂停宵禁失败：{e}"
        )


    paused_until = data.get(
        "paused_until_local"
    )


    if paused_until:

        return (
            f"已暂停宵禁提醒 "
            f"{minutes} 分钟。"
            f"恢复时间："
            f"{paused_until}"
        )


    return (
        f"已暂停宵禁提醒 "
        f"{minutes} 分钟。"
    )


# =========================
# 宵禁：今晚放行
# =========================

def curfew_allow_tonight():

    if not ORIGIN_API:

        return (
            "放行失败："
            "未配置 ORIGIN_API"
        )


    if not AUTH_TOKEN:

        return (
            "放行失败："
            "未配置 AUTH_TOKEN"
        )


    try:

        data = post_json(
            f"{ORIGIN_API}"
            "/curfew/allow-tonight"
        )

    except Exception as e:

        return (
            f"今晚放行失败：{e}"
        )


    paused_until = data.get(
        "paused_until_local"
    )


    if paused_until:

        return (
            "今晚的宵禁提醒已暂停，"
            "会在早上 06:00 "
            "自动恢复。"
        )


    return (
        "今晚的宵禁提醒已暂停。"
    )


# =========================
# 宵禁：恢复
# =========================

def curfew_resume():

    if not ORIGIN_API:

        return (
            "恢复失败："
            "未配置 ORIGIN_API"
        )


    if not AUTH_TOKEN:

        return (
            "恢复失败："
            "未配置 AUTH_TOKEN"
        )


    try:

        post_json(
            f"{ORIGIN_API}"
            "/curfew/resume"
        )

    except Exception as e:

        return (
            f"恢复宵禁失败：{e}"
        )


    return (
        "宵禁提醒已经恢复。"
    )


# =========================
# 获取未读提醒
# =========================

def get_pending_reminders():

    if not ORIGIN_API:

        return (
            "读取提醒失败："
            "未配置 ORIGIN_API"
        )


    if not AUTH_TOKEN:

        return (
            "读取提醒失败："
            "未配置 AUTH_TOKEN"
        )


    try:

        data = post_json(
            f"{ORIGIN_API}"
            "/reminders/consume"
        )

    except Exception as e:

        return (
            f"读取未读提醒失败：{e}"
        )


    reminders = data.get(
        "reminders",
        []
    )


    if not reminders:

        return (
            "目前没有未读的系统提醒。"
        )


    lines = [
        f"共有 {len(reminders)} 条未读提醒："
    ]


    for reminder in reminders:

        title = (
            reminder.get(
                "title"
            )
            or
            "系统提醒"
        )

        content = (
            reminder.get(
                "content"
            )
            or
            ""
        )

        created_at = (
            reminder.get(
                "created_at_local"
            )
            or
            ""
        )


        time_text = created_at


        if "T" in created_at:

            try:

                time_part = (
                    created_at
                    .split(
                        "T",
                        1
                    )[1]
                )

                time_text = (
                    time_part[
                        :5
                    ]
                )

            except Exception:

                pass


        lines.append(
            f"[{time_text}] "
            f"{title}："
            f"{content}"
        )


    return "\n".join(
        lines
    )


# =========================
# MCP 工具定义
# =========================

TOOLS = [

    {
        "name":
            "check_on_wife",

        "description":
            "查询手机最近打开的 App "
            "和已经计算好的使用时长",

        "inputSchema": {

            "type":
                "object",

            "properties": {

                "limit": {
                    "type":
                        "integer",

                    "description":
                        "最多返回多少条"
                        "最近 App 记录",

                    "default":
                        10
                }

            }

        }

    },

    {
        "name":
            "bark_alert",

        "description":
            "通过 Bark 给手机发送通知",

        "inputSchema": {

            "type":
                "object",

            "properties": {

                "title": {
                    "type":
                        "string",

                    "description":
                        "通知标题",

                    "default":
                        "Robin"
                },

                "content": {
                    "type":
                        "string",

                    "description":
                        "通知正文"
                }

            },

            "required": [
                "content"
            ]

        }

    },

    {
        "name":
            "curfew_pause",

        "description":
            "当用户明确表示想晚一点睡、"
            "暂时不要继续提醒时，"
            "暂停宵禁 Bark 提醒指定分钟。",

        "inputSchema": {

            "type":
                "object",

            "properties": {

                "minutes": {
                    "type":
                        "integer",

                    "description":
                        "暂停多少分钟，"
                        "例如30、60、120",

                    "default":
                        60
                }

            }

        }

    },

    {
        "name":
            "curfew_allow_tonight",

        "description":
            "当用户明确表示今晚不想再收到"
            "宵禁提醒时使用。"
            "暂停今晚剩余的提醒，"
            "早上06:00自动恢复。",

        "inputSchema": {

            "type":
                "object",

            "properties": {}

        }

    },

    {
        "name":
            "curfew_resume",

        "description":
            "立即取消宵禁暂停状态，"
            "恢复正常的夜间提醒。",

        "inputSchema": {

            "type":
                "object",

            "properties": {}

        }

    },

    {
        "name":
            "get_pending_reminders",

        "description":
            "读取 Railway 后台尚未在聊天中"
            "处理过的系统提醒。"
            "当用户发送新的聊天消息时，"
            "尤其在夜间，优先调用此工具检查"
            "是否有 Robin 之前通过 Bark "
            "发送过但用户尚未在聊天中看到的提醒。"
            "读取后这些提醒会被标记为已读，"
            "避免重复显示。",

        "inputSchema": {

            "type":
                "object",

            "properties": {}

        }

    }

]


FUNCTIONS = {

    "check_on_wife":
        check_on_wife,

    "bark_alert":
        bark_alert,

    "curfew_pause":
        curfew_pause,

    "curfew_allow_tonight":
        curfew_allow_tonight,

    "curfew_resume":
        curfew_resume,

    "get_pending_reminders":
        get_pending_reminders,

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
            "bark_alert",
            "curfew_pause",
            "curfew_allow_tonight",
            "curfew_resume",
            "get_pending_reminders"
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
                        "1.2"
                }

            }

        }


    if method == "notifications/initialized":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {}

        }


    if method == "ping":

        return {

            "jsonrpc":
                "2.0",

            "id":
                request_id,

            "result": {}

        }


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

            result = (
                FUNCTIONS[
                    name
                ](
                    **args
                )
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
                            str(
                                result
                            )
                    }

                ]

            }

        }


    return {

        "jsonrpc":
            "2.0",

        "id":
            request_id,

        "error": {

            "code":
                -32601,

            "message":
                (
                    "Unknown method: "
                    f"{method}"
                )

        }

    }


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
