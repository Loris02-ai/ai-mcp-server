from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

import json
import os
import uvicorn

from urllib.parse import quote
from urllib.request import Request as URLRequest
from urllib.request import urlopen


# =========================
# 标准 Health MCP（官方 MCP Python SDK v2）
# =========================
#
# 为了不破坏你现在已经能用的 /mcp（查岗、Bark、宵禁等），
# 健康 MCP 先独立挂载在：
#
#   /health/mcp
#
# 等 Kelivo / Echoes 都验证成功后，再决定是否把所有工具合并到同一个标准 MCP。
#

health_mcp = MCPServer(
    "Echoes Health MCP"
)

health_mcp_http_app = health_mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(
        # Railway 前面有反向代理。
        # 第一阶段先关闭 SDK 的 DNS rebinding Host 限制，
        # 否则公网 Railway 域名会收到 421。
        enable_dns_rebinding_protection=False
    )
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):

    async with health_mcp.session_manager.run():
        yield


app = FastAPI(
    title="Echoes MCP Server",
    lifespan=lifespan
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
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
# 未读提醒内部工具
# =========================

def consume_pending_reminders():

    if not ORIGIN_API:

        return []


    if not AUTH_TOKEN:

        return []


    try:

        data = post_json(
            f"{ORIGIN_API}"
            "/reminders/consume"
        )

    except Exception:

        return []


    reminders = data.get(
        "reminders",
        []
    )


    if not isinstance(
        reminders,
        list
    ):

        return []


    return reminders


def format_reminders(
    reminders
):

    if not reminders:

        return ""


    lines = []


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
# 同时读取刚刚的未读提醒
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


    # 先执行暂停
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


    # 暂停成功后，
    # 自动读取刚刚的系统提醒
    reminders = (
        consume_pending_reminders()
    )

    reminder_text = (
        format_reminders(
            reminders
        )
    )


    lines = []


    if reminder_text:

        lines.append(
            "刚刚有未读的宵禁提醒："
        )

        lines.append(
            reminder_text
        )


    if paused_until:

        lines.append(
            f"已暂停宵禁提醒 "
            f"{minutes} 分钟。"
        )

        lines.append(
            f"恢复时间："
            f"{paused_until}"
        )

    else:

        lines.append(
            f"已暂停宵禁提醒 "
            f"{minutes} 分钟。"
        )


    return "\n".join(
        lines
    )


# =========================
# 宵禁：今晚放行
# 同时读取刚刚的未读提醒
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


    # 先执行今晚放行
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


    # 放行成功以后，
    # 自动读取刚刚的系统提醒
    reminders = (
        consume_pending_reminders()
    )

    reminder_text = (
        format_reminders(
            reminders
        )
    )


    lines = []


    if reminder_text:

        lines.append(
            "刚刚有未读的宵禁提醒："
        )

        lines.append(
            reminder_text
        )


    if paused_until:

        lines.append(
            "今晚的宵禁提醒已暂停，"
            "会在早上 06:00 "
            "自动恢复。"
        )

    else:

        lines.append(
            "今晚的宵禁提醒已暂停。"
        )


    return "\n".join(
        lines
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
# 手动获取未读提醒
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


    reminders = (
        consume_pending_reminders()
    )


    if not reminders:

        return (
            "目前没有未读的系统提醒。"
        )


    reminder_text = (
        format_reminders(
            reminders
        )
    )


    return (
        f"共有 {len(reminders)} "
        "条未读提醒：\n"
        f"{reminder_text}"
    )



# =========================
# 健康数据：第一阶段临时存储
# =========================
#
# 这一版的目的：
# 1. 先验证 Kelivo / Echoes 能否正确连接“标准 Streamable HTTP MCP”
# 2. 再接 iPhone / Apple Health 自动上传
#
# Railway 的普通文件系统可能在重新部署/重启后丢失，
# 因此这里暂时只用于第一阶段测试。
# 后面确认链路正常后，再换成持久化存储。
#

HEALTH_DATA_FILE = Path(
    os.environ.get(
        "HEALTH_DATA_FILE",
        "/tmp/echoes_health_data.json"
    )
)


def empty_health_data():

    return {
        "status": "no_data",
        "received_at": None,
        "source": None,

        "heart_rate": {
            "latest_bpm": None,
            "resting_bpm": None,
            "min_bpm": None,
            "max_bpm": None,
            "recorded_at": None
        },

        "oxygen": {
            "latest_percent": None,
            "min_percent": None,
            "max_percent": None,
            "recorded_at": None
        },

        "steps": {
            "count": None,
            "date": None
        },

        "sleep": {
            "status": "no_data",
            "date": None,
            "total_minutes": None,
            "deep_minutes": None,
            "light_minutes": None,
            "rem_minutes": None,
            "awake_minutes": None,
            "sleep_start": None,
            "sleep_end": None
        }
    }


def read_health_data():

    if not HEALTH_DATA_FILE.exists():

        return empty_health_data()


    try:

        with HEALTH_DATA_FILE.open(
            "r",
            encoding="utf-8"
        ) as fp:

            data = json.load(
                fp
            )


        if not isinstance(
            data,
            dict
        ):

            return empty_health_data()


        return data


    except Exception:

        return empty_health_data()


def save_health_data(
    data
):

    HEALTH_DATA_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    with HEALTH_DATA_FILE.open(
        "w",
        encoding="utf-8"
    ) as fp:

        json.dump(
            data,
            fp,
            ensure_ascii=False,
            indent=2
        )


def merge_dict(
    base,
    incoming
):

    if not isinstance(
        incoming,
        dict
    ):

        return base


    for key, value in incoming.items():

        if (
            isinstance(
                value,
                dict
            )
            and
            isinstance(
                base.get(key),
                dict
            )
        ):

            base[key] = merge_dict(
                base[key],
                value
            )

        else:

            base[key] = value


    return base


# =========================
# 健康数据 HTTP 接口
# =========================

@app.get("/health/status")
def health_status():

    data = read_health_data()

    return {
        "status": "online",
        "health_mcp": "/health/mcp",
        "has_health_data":
            data.get("status") == "ok",
        "received_at":
            data.get("received_at")
    }


@app.post("/health/upload")
async def health_upload(
    request: Request
):

    # 上传健康数据必须验证 AUTH_TOKEN。
    # 以后 Health Auto Export / 快捷指令会把：
    #
    #   Authorization: Bearer 你的 AUTH_TOKEN
    #
    # 一起发送过来。
    if not AUTH_TOKEN:

        raise HTTPException(
            status_code=503,
            detail=(
                "服务器尚未配置 AUTH_TOKEN，"
                "已拒绝健康数据上传。"
            )
        )


    authorization = (
        request.headers
        .get(
            "Authorization",
            ""
        )
        .strip()
    )


    if authorization != f"Bearer {AUTH_TOKEN}":

        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )


    payload = await request.json()


    if not isinstance(
        payload,
        dict
    ):

        raise HTTPException(
            status_code=400,
            detail="JSON body 必须是对象"
        )


    current = read_health_data()


    if current.get("status") != "ok":

        current = empty_health_data()


    merged = merge_dict(
        current,
        payload
    )


    merged["status"] = "ok"

    merged["received_at"] = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )


    if not merged.get("source"):

        merged["source"] = (
            "apple_health"
        )


    save_health_data(
        merged
    )


    return {
        "ok": True,
        "received_at":
            merged["received_at"]
    }


# =========================
# 标准 Health MCP 工具
# =========================

@health_mcp.tool()
def get_latest_health() -> dict[str, Any]:
    """
    查询最近一次已经同步到服务器的健康数据总览。
    当用户询问自己最近的心率、静息心率、血氧、步数、睡眠，
    或希望查看整体健康记录时使用。
    数据来自穿戴设备/健康 App，仅用于查看记录，不代表医学诊断。
    """

    return read_health_data()


@health_mcp.tool()
def get_heart_rate() -> dict[str, Any]:
    """
    查询最近同步的心率记录，包括最近心率、静息心率、
    最低心率、最高心率和记录时间。
    当用户询问心率、脉搏或静息心率时使用。
    """

    data = read_health_data()

    return {
        "status":
            data.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "heart_rate":
            data.get(
                "heart_rate",
                {}
            )
    }


@health_mcp.tool()
def get_steps() -> dict[str, Any]:
    """
    查询最近同步的步数记录。
    当用户询问今天走了多少步、活动量或步数时使用。
    """

    data = read_health_data()

    return {
        "status":
            data.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "steps":
            data.get(
                "steps",
                {}
            )
    }


@health_mcp.tool()
def get_oxygen() -> dict[str, Any]:
    """
    查询最近同步的血氧饱和度记录。
    当用户询问血氧、SpO2 或最近血氧记录时使用。
    这是穿戴设备记录，不作为医学诊断。
    """

    data = read_health_data()

    return {
        "status":
            data.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "oxygen":
            data.get(
                "oxygen",
                {}
            )
    }


@health_mcp.tool()
def get_sleep() -> dict[str, Any]:
    """
    查询最近同步的睡眠记录，包括总睡眠时长和可用的睡眠阶段。
    当用户询问昨晚睡了多久、睡眠记录或睡眠阶段时使用。
    如果手环没有佩戴、没有同步或当前没有近期睡眠数据，
    会返回 no_data；不能把 no_data 解释为用户没有睡觉。
    """

    data = read_health_data()

    sleep = data.get(
        "sleep",
        {}
    )


    if not isinstance(
        sleep,
        dict
    ):

        sleep = {
            "status":
                "no_data"
        }


    return {
        "status":
            data.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "sleep":
            sleep,

        "important_note": (
            "如果 sleep.status 为 no_data，"
            "只表示没有可用的手环/同步记录，"
            "不能据此判断用户没有睡觉。"
        )
    }


@health_mcp.tool()
def get_health_summary() -> dict[str, Any]:
    """
    获取适合 AI 总结的最近健康数据摘要。
    当用户询问“看看我今天/最近的身体数据”“帮我总结健康记录”
    或同时涉及心率、血氧、步数、睡眠中的多项数据时使用。
    """

    data = read_health_data()

    return {
        "status":
            data.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "source":
            data.get(
                "source"
            ),

        "heart_rate":
            data.get(
                "heart_rate",
                {}
            ),

        "oxygen":
            data.get(
                "oxygen",
                {}
            ),

        "steps":
            data.get(
                "steps",
                {}
            ),

        "sleep":
            data.get(
                "sleep",
                {}
            ),

        "note": (
            "这些是穿戴设备/健康 App 的记录，"
            "只用于查看和整理，不代表医学诊断。"
        )
    }



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
            "当用户在收到宵禁提醒后表示"
            "还想再玩一会儿、晚一点睡，"
            "自动读取刚才尚未处理的系统提醒，"
            "让 Robin 知道刚才提醒了什么，"
            "然后暂停宵禁 Bark 提醒指定分钟。"
            "例如用户说再玩10分钟时，"
            "应使用 minutes=10。",

        "inputSchema": {

            "type":
                "object",

            "properties": {

                "minutes": {
                    "type":
                        "integer",

                    "description":
                        "暂停多少分钟，"
                        "根据用户明确说的时间填写。"
                        "例如再玩10分钟就填10，"
                        "半小时就填30，"
                        "一小时就填60。",

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
            "当用户在收到宵禁提醒后明确表示"
            "今晚要熬夜、今晚不想睡、"
            "今晚不要再提醒时使用。"
            "工具会自动读取刚才尚未处理的"
            "系统提醒，让 Robin 知道提醒内容，"
            "然后暂停今晚剩余的 Bark 宵禁提醒，"
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
            "恢复正常的夜间提醒。"
            "例如用户之前说今晚不提醒，"
            "后来又要求恢复提醒时使用。",

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
            "手动读取 Railway 后台尚未在聊天中"
            "处理过的系统提醒。"
            "如果用户只是询问有没有未读提醒，"
            "可以调用此工具。"
            "curfew_pause 和 "
            "curfew_allow_tonight "
            "已经会自动读取未读宵禁提醒，"
            "无需额外再调用一次。"
            "读取后提醒会标记为已读。",

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

        "health_mcp":
            "/health/mcp",

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
                        "1.3"
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


# =========================
# 挂载标准 Health MCP
# =========================
#
# 对外地址：
#   https://你的-Railway-域名/health/mcp
#
# 注意：必须放在现有 FastAPI 路由定义之后，
# 这样不会影响原来的 /mcp、/health/upload 等接口。
#

app.mount(
    "/health",
    health_mcp_http_app
)


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
