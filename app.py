from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

import asyncio
import contextlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

import json
import os
import threading
import uvicorn
import httpx

from urllib.parse import quote
from urllib.request import Request as URLRequest
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


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


def env_positive_int(
    name,
    default=None
):

    raw = os.environ.get(
        name,
        ""
    ).strip()

    if not raw:

        return default

    try:

        value = int(
            raw
        )

    except Exception:

        return default

    if value <= 0:

        return default

    return value


PERIOD_TYPICAL_CYCLE_DAYS = env_positive_int(
    "PERIOD_TYPICAL_CYCLE_DAYS"
)

PERIOD_TYPICAL_DURATION_DAYS = env_positive_int(
    "PERIOD_TYPICAL_DURATION_DAYS"
)


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
        },

        "body_measurements": {
            "weight": {
                "value": None,
                "unit": None,
                "recorded_at": None
            },
            "body_fat_percentage": {
                "value": None,
                "unit": "%",
                "recorded_at": None
            },
            "lean_body_mass": {
                "value": None,
                "unit": None,
                "recorded_at": None
            },
            "height": {
                "value": None,
                "unit": None,
                "recorded_at": None
            }
        },

        "period_tracking": {
            "status": "no_data",
            "last_period": None,
            "recent_period_starts": [],
            "daily_records": [],
            "prediction": None
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
# Health Auto Export JSON V2 解析
# =========================

def to_float(
    value
):

    try:

        if value is None:

            return None


        return float(
            value
        )

    except Exception:

        return None


def round_number(
    value,
    digits=1
):

    number = to_float(
        value
    )


    if number is None:

        return None


    rounded = round(
        number,
        digits
    )


    if float(
        rounded
    ).is_integer():

        return int(
            rounded
        )


    return rounded


def latest_metric_item(
    items
):

    if not isinstance(
        items,
        list
    ):

        return None


    valid = [
        item
        for item in items
        if isinstance(
            item,
            dict
        )
    ]


    if not valid:

        return None


    # Health Auto Export 的日期字符串本身按
    # yyyy-MM-dd HH:mm:ss Z 排序即可满足这里的“最近一条”用途。
    return max(
        valid,
        key=lambda item:
            str(
                item.get(
                    "date",
                    ""
                )
            )
    )


def normalize_percent(
    value
):

    number = to_float(
        value
    )


    if number is None:

        return None


    # 某些来源可能用 0~1 表示百分比。
    if 0 <= number <= 1:

        number = (
            number
            * 100
        )


    return round_number(
        number,
        1
    )


def hours_to_minutes(
    value
):

    hours = to_float(
        value
    )


    if hours is None:

        return None


    return int(
        round(
            hours
            * 60
        )
    )


def metric_map_from_payload(
    payload
):

    data = payload.get(
        "data",
        {}
    )


    if not isinstance(
        data,
        dict
    ):

        return {}


    metrics = data.get(
        "metrics",
        []
    )


    if not isinstance(
        metrics,
        list
    ):

        return {}


    result = {}


    for metric in metrics:

        if not isinstance(
            metric,
            dict
        ):

            continue


        name = str(
            metric.get(
                "name",
                ""
            )
        ).strip()


        if not name:

            continue


        result[
            name
        ] = metric


    return result


def metric_by_names(
    metrics,
    names
):

    for name in names:

        metric = metrics.get(
            name
        )

        if isinstance(
            metric,
            dict
        ):

            return metric


    return None


def latest_simple_measurement(
    metric,
    percent=False
):

    if not isinstance(
        metric,
        dict
    ):

        return None


    latest = latest_metric_item(
        metric.get(
            "data",
            []
        )
    )


    if not latest:

        return None


    raw_value = latest.get(
        "qty"
    )


    value = (
        normalize_percent(
            raw_value
        )
        if percent
        else round_number(
            raw_value,
            2
        )
    )


    if value is None:

        return None


    unit = metric.get(
        "units"
    )


    if percent:

        unit = "%"


    return {
        "value": value,
        "unit": unit,
        "recorded_at": latest.get(
            "date"
        )
    }



# =========================
# 月经周期跟踪解析与预测
# =========================
#
# 只读取 Health Auto Export 的 Menstrual Flow（月经记录）。
# 不保存症状、性行为、妊娠/排卵测试等其他周期跟踪项目。
#
# 预测规则：
# - 预测经期：根据最近最多 6 个有效周期的起始间隔取平均值
# - 预测排卵日：预计下次经期开始日前 13 天
# - 预测排卵期：根据最近周期长度的最短/最长波动，
#   给出“可能的预计排卵日期范围”
#
# 所有预测都只是基于历史记录的日历估算，
# 不能确认实际排卵，也不用于诊断或避孕判断。
# =========================

def parse_export_datetime(
    value
):

    if not value:

        return None


    text_value = str(
        value
    ).strip()


    formats = [
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d"
    ]


    for fmt in formats:

        try:

            return datetime.strptime(
                text_value,
                fmt
            )

        except Exception:

            pass


    try:

        return datetime.fromisoformat(
            text_value
        )

    except Exception:

        return None


def expand_entry_dates(
    start_dt,
    end_dt
):

    if start_dt is None:

        return []


    start_day = start_dt.date()

    end_day = (
        end_dt.date()
        if end_dt is not None
        else start_day
    )


    if end_day < start_day:

        end_day = start_day


    days = []

    current_day = start_day


    while (
        current_day <= end_day
        and
        len(days) < 31
    ):

        days.append(
            current_day
        )

        current_day = (
            current_day
            + timedelta(days=1)
        )


    return days


def flow_rank(
    value
):

    ranks = {
        "none": 0,
        "unspecified": 1,
        "light": 2,
        "medium": 3,
        "heavy": 4
    }


    return ranks.get(
        str(value).strip().lower(),
        1
    )


def merge_daily_period_records(
    existing_records,
    incoming_records
):

    merged = {}


    for source_records in [
        existing_records,
        incoming_records
    ]:

        if not isinstance(
            source_records,
            list
        ):

            continue


        for item in source_records:

            if not isinstance(
                item,
                dict
            ):

                continue


            day = str(
                item.get(
                    "date",
                    ""
                )
            )[:10]


            if not day:

                continue


            previous = merged.get(
                day
            )


            if previous is None:

                merged[
                    day
                ] = {
                    "date":
                        day,

                    "flow":
                        item.get(
                            "flow"
                        ),

                    "is_cycle_start":
                        bool(
                            item.get(
                                "is_cycle_start",
                                False
                            )
                        )
                }

                continue


            if flow_rank(
                item.get(
                    "flow"
                )
            ) > flow_rank(
                previous.get(
                    "flow"
                )
            ):

                previous[
                    "flow"
                ] = item.get(
                    "flow"
                )


            if item.get(
                "is_cycle_start"
            ):

                previous[
                    "is_cycle_start"
                ] = True


    return [
        merged[
            key
        ]
        for key in sorted(
            merged.keys()
        )
    ]


def group_period_days(
    daily_records
):

    dated = []


    for item in daily_records:

        if not isinstance(
            item,
            dict
        ):

            continue


        try:

            day = datetime.strptime(
                str(
                    item.get(
                        "date",
                        ""
                    )
                )[:10],
                "%Y-%m-%d"
            ).date()

        except Exception:

            continue


        dated.append(
            (
                day,
                item
            )
        )


    dated.sort(
        key=lambda pair:
            pair[0]
    )


    groups = []


    for day, item in dated:

        if not groups:

            groups.append(
                [
                    (
                        day,
                        item
                    )
                ]
            )

            continue


        previous_day = (
            groups[-1][-1][0]
        )


        gap = (
            day
            - previous_day
        ).days


        # 允许中间漏记 1 天，避免把同一次经期错误拆成两段。
        if gap <= 2:

            groups[-1].append(
                (
                    day,
                    item
                )
            )

        else:

            groups.append(
                [
                    (
                        day,
                        item
                    )
                ]
            )


    return groups


def calculate_period_summary(
    daily_records
):

    groups = group_period_days(
        daily_records
    )


    if not groups:

        return {
            "last_period": None,
            "recent_period_starts": [],
            "prediction": None
        }


    periods = []


    for group in groups:

        start_day = group[0][0]
        end_day = group[-1][0]

        explicit_starts = [
            day
            for day, item in group
            if item.get(
                "is_cycle_start"
            )
        ]


        actual_start = (
            min(
                explicit_starts
            )
            if explicit_starts
            else start_day
        )


        periods.append(
            {
                "start_date":
                    actual_start.isoformat(),

                "end_date":
                    end_day.isoformat(),

                "duration_days":
                    (
                        end_day
                        - actual_start
                    ).days
                    + 1
            }
        )


    periods.sort(
        key=lambda item:
            item[
                "start_date"
            ]
    )


    last_period = periods[-1]


    recent_starts = [
        item[
            "start_date"
        ]
        for item in periods[-8:]
    ]


    cycle_intervals = []


    for index in range(
        1,
        len(periods)
    ):

        try:

            previous = datetime.strptime(
                periods[
                    index - 1
                ][
                    "start_date"
                ],
                "%Y-%m-%d"
            ).date()

            current = datetime.strptime(
                periods[
                    index
                ][
                    "start_date"
                ],
                "%Y-%m-%d"
            ).date()

        except Exception:

            continue


        interval = (
            current
            - previous
        ).days


        if 15 <= interval <= 90:

            cycle_intervals.append(
                interval
            )


    recent_intervals = (
        cycle_intervals[-6:]
    )


    period_lengths = [
        item[
            "duration_days"
        ]
        for item in periods[-6:]
        if 1 <= item.get(
            "duration_days",
            0
        ) <= 15
    ]


    try:

        last_start = datetime.strptime(
            last_period[
                "start_date"
            ],
            "%Y-%m-%d"
        ).date()

    except Exception:

        last_start = None


    prediction = None


    # -------------------------
    # 方案 A：有至少两个真实周期
    # 优先使用真实历史间隔
    # -------------------------

    if (
        recent_intervals
        and
        last_start is not None
    ):

        average_cycle = int(
            round(
                sum(
                    recent_intervals
                )
                / len(
                    recent_intervals
                )
            )
        )


        average_period = (
            int(
                round(
                    sum(
                        period_lengths
                    )
                    / len(
                        period_lengths
                    )
                )
            )
            if period_lengths
            else PERIOD_TYPICAL_DURATION_DAYS
        )


        predicted_start = (
            last_start
            + timedelta(
                days=average_cycle
            )
        )


        predicted_end = (
            predicted_start
            + timedelta(
                days=max(
                    average_period - 1,
                    0
                )
            )
            if average_period is not None
            else None
        )


        predicted_ovulation_day = (
            predicted_start
            - timedelta(
                days=13
            )
        )


        shortest_cycle = min(
            recent_intervals
        )

        longest_cycle = max(
            recent_intervals
        )


        predicted_ovulation_period_start = (
            last_start
            + timedelta(
                days=shortest_cycle - 13
            )
        )

        predicted_ovulation_period_end = (
            last_start
            + timedelta(
                days=longest_cycle - 13
            )
        )


        prediction = {
            "predicted_period_start":
                predicted_start.isoformat(),

            "predicted_period_end":
                (
                    predicted_end.isoformat()
                    if predicted_end is not None
                    else None
                ),

            "predicted_ovulation_day":
                predicted_ovulation_day.isoformat(),

            "predicted_ovulation_period_start":
                predicted_ovulation_period_start.isoformat(),

            "predicted_ovulation_period_end":
                predicted_ovulation_period_end.isoformat(),

            "average_cycle_length_days":
                average_cycle,

            "average_period_length_days":
                average_period,

            "cycle_interval_min_days":
                shortest_cycle,

            "cycle_interval_max_days":
                longest_cycle,

            "based_on_cycle_intervals":
                len(
                    recent_intervals
                ),

            "method":
                "calendar_estimate_from_logged_period_history",

            "prediction_basis":
                "logged_period_history",

            "important_note":
                (
                    "预测经期、预测排卵日和预测排卵期"
                    "都只是根据已记录经期历史进行的日历估算。"
                    "它们不代表实际已经发生，也不能确认实际排卵，"
                    "不用于诊断或避孕判断。"
                )
        }


    # -------------------------
    # 方案 B：只有一个真实周期
    # 使用 Railway 配置的“典型周期长度/典型经期长度”
    # 作为备用日历预测。
    #
    # 如果上一次真实经期距离今天很久，
    # 会按典型周期长度逐次向前推，
    # 直到得到今天或之后的下一次预测经期。
    # -------------------------

    elif (
        last_start is not None
        and
        PERIOD_TYPICAL_CYCLE_DAYS is not None
        and
        PERIOD_TYPICAL_DURATION_DAYS is not None
    ):

        typical_cycle = (
            PERIOD_TYPICAL_CYCLE_DAYS
        )

        typical_period = (
            PERIOD_TYPICAL_DURATION_DAYS
        )


        predicted_start = (
            last_start
            + timedelta(
                days=typical_cycle
            )
        )


        today = datetime.now(
            timezone.utc
        ).date()


        projected_cycles = 1


        while (
            predicted_start < today
            and
            projected_cycles < 120
        ):

            predicted_start = (
                predicted_start
                + timedelta(
                    days=typical_cycle
                )
            )

            projected_cycles += 1


        predicted_end = (
            predicted_start
            + timedelta(
                days=max(
                    typical_period - 1,
                    0
                )
            )
        )


        predicted_ovulation_day = (
            predicted_start
            - timedelta(
                days=13
            )
        )


        # 只有一个真实周期时，没有真实的周期波动范围。
        # 因此“预测排卵期”暂时退化为同一天，
        # 明确标记为无法根据历史估计范围。
        predicted_ovulation_period_start = (
            predicted_ovulation_day
        )

        predicted_ovulation_period_end = (
            predicted_ovulation_day
        )


        prediction = {
            "predicted_period_start":
                predicted_start.isoformat(),

            "predicted_period_end":
                predicted_end.isoformat(),

            "predicted_ovulation_day":
                predicted_ovulation_day.isoformat(),

            "predicted_ovulation_period_start":
                predicted_ovulation_period_start.isoformat(),

            "predicted_ovulation_period_end":
                predicted_ovulation_period_end.isoformat(),

            "average_cycle_length_days":
                typical_cycle,

            "average_period_length_days":
                typical_period,

            "cycle_interval_min_days":
                None,

            "cycle_interval_max_days":
                None,

            "based_on_cycle_intervals":
                0,

            "projected_cycles_from_last_record":
                projected_cycles,

            "method":
                "calendar_estimate_from_configured_typical_cycle",

            "prediction_basis":
                "configured_typical_cycle_fallback",

            "ovulation_period_range_status":
                "single_day_only_insufficient_history_for_range",

            "important_note":
                (
                    "当前真实经期历史不足两个周期，"
                    "所以这组预测使用 Railway 中配置的典型周期长度"
                    "和典型经期长度进行备用日历推算。"
                    "预测经期、预测排卵日和预测排卵期"
                    "都不是实际发生记录，也不能确认实际排卵。"
                    "由于没有足够的真实周期波动数据，"
                    "预测排卵期暂时只显示预测排卵日这一天，"
                    "不额外虚构范围。"
                    "这些预测不用于诊断或避孕判断。"
                )
        }


    return {
        "last_period":
            last_period,

        "recent_period_starts":
            recent_starts,

        "prediction":
            prediction
    }


def parse_cycle_tracking(
    payload,
    existing_period_tracking=None
):

    data = payload.get(
        "data",
        {}
    )


    if not isinstance(
        data,
        dict
    ):

        return None


    entries = data.get(
        "cycleTracking",
        []
    )


    if not isinstance(
        entries,
        list
    ):

        return None


    incoming_records = []


    for entry in entries:

        if not isinstance(
            entry,
            dict
        ):

            continue


        name = str(
            entry.get(
                "name",
                ""
            )
        ).strip().lower()


        if name not in {
            "menstrual flow",
            "menstrual_flow",
            "月经流量"
        }:

            continue


        flow = str(
            entry.get(
                "value",
                "Unspecified"
            )
        ).strip()


        if flow.lower() == "none":

            continue


        start_dt = parse_export_datetime(
            entry.get(
                "start"
            )
        )

        end_dt = parse_export_datetime(
            entry.get(
                "end"
            )
        )


        entry_days = expand_entry_dates(
            start_dt,
            end_dt
        )


        for index, day in enumerate(
            entry_days
        ):

            incoming_records.append(
                {
                    "date":
                        day.isoformat(),

                    "flow":
                        flow,

                    "is_cycle_start":
                        bool(
                            entry.get(
                                "isCycleStart",
                                False
                            )
                        )
                        and
                        index == 0
                }
            )


    if not incoming_records:

        return None


    existing_records = []


    if isinstance(
        existing_period_tracking,
        dict
    ):

        existing_records = (
            existing_period_tracking.get(
                "daily_records",
                []
            )
        )


    merged_records = merge_daily_period_records(
        existing_records,
        incoming_records
    )


    summary = calculate_period_summary(
        merged_records
    )


    return {
        "status":
            "ok",

        "last_period":
            summary.get(
                "last_period"
            ),

        "recent_period_starts":
            summary.get(
                "recent_period_starts",
                []
            ),

        "daily_records":
            merged_records,

        "prediction":
            summary.get(
                "prediction"
            )
    }


def parse_health_auto_export(
    payload
):

    metrics = metric_map_from_payload(
        payload
    )


    if not metrics:

        return None


    parsed = {
        "source":
            "health_auto_export"
    }


    # -------------------------
    # 心率
    # -------------------------

    heart_metric = metrics.get(
        "heart_rate"
    )


    if isinstance(
        heart_metric,
        dict
    ):

        heart_items = heart_metric.get(
            "data",
            []
        )


        latest = latest_metric_item(
            heart_items
        )


        min_values = []
        max_values = []


        if isinstance(
            heart_items,
            list
        ):

            for item in heart_items:

                if not isinstance(
                    item,
                    dict
                ):

                    continue


                min_value = to_float(
                    item.get(
                        "Min",
                        item.get(
                            "min"
                        )
                    )
                )

                max_value = to_float(
                    item.get(
                        "Max",
                        item.get(
                            "max"
                        )
                    )
                )


                if min_value is not None:

                    min_values.append(
                        min_value
                    )


                if max_value is not None:

                    max_values.append(
                        max_value
                    )


        if latest:

            latest_value = (
                latest.get(
                    "Avg"
                )
                if latest.get(
                    "Avg"
                ) is not None
                else latest.get(
                    "avg"
                )
            )


            if latest_value is None:

                latest_value = latest.get(
                    "qty"
                )


            parsed[
                "heart_rate"
            ] = {
                "latest_bpm":
                    round_number(
                        latest_value
                    ),

                "min_bpm":
                    round_number(
                        min(
                            min_values
                        )
                    )
                    if min_values
                    else None,

                "max_bpm":
                    round_number(
                        max(
                            max_values
                        )
                    )
                    if max_values
                    else None,

                "recorded_at":
                    latest.get(
                        "date"
                    )
            }


    # -------------------------
    # 静息心率
    # -------------------------

    resting_metric = metrics.get(
        "resting_heart_rate"
    )


    if isinstance(
        resting_metric,
        dict
    ):

        latest_resting = latest_metric_item(
            resting_metric.get(
                "data",
                []
            )
        )


        if latest_resting:

            parsed.setdefault(
                "heart_rate",
                {}
            )


            parsed[
                "heart_rate"
            ][
                "resting_bpm"
            ] = round_number(
                latest_resting.get(
                    "qty"
                )
            )


    # -------------------------
    # 血氧
    # -------------------------

    oxygen_metric = metrics.get(
        "blood_oxygen_saturation"
    )


    if isinstance(
        oxygen_metric,
        dict
    ):

        oxygen_items = oxygen_metric.get(
            "data",
            []
        )


        latest_oxygen = latest_metric_item(
            oxygen_items
        )


        oxygen_values = []


        if isinstance(
            oxygen_items,
            list
        ):

            for item in oxygen_items:

                if not isinstance(
                    item,
                    dict
                ):

                    continue


                value = normalize_percent(
                    item.get(
                        "qty"
                    )
                )


                if value is not None:

                    oxygen_values.append(
                        value
                    )


        if latest_oxygen:

            latest_percent = normalize_percent(
                latest_oxygen.get(
                    "qty"
                )
            )


            parsed[
                "oxygen"
            ] = {
                "latest_percent":
                    latest_percent,

                "min_percent":
                    min(
                        oxygen_values
                    )
                    if oxygen_values
                    else latest_percent,

                "max_percent":
                    max(
                        oxygen_values
                    )
                    if oxygen_values
                    else latest_percent,

                "recorded_at":
                    latest_oxygen.get(
                        "date"
                    )
            }


    # -------------------------
    # 步数
    # -------------------------

    steps_metric = metrics.get(
        "step_count"
    )


    if isinstance(
        steps_metric,
        dict
    ):

        step_items = steps_metric.get(
            "data",
            []
        )


        valid_steps = [
            item
            for item in step_items
            if isinstance(
                item,
                dict
            )
        ] if isinstance(
            step_items,
            list
        ) else []


        if valid_steps:

            latest_step = latest_metric_item(
                valid_steps
            )


            latest_date = str(
                latest_step.get(
                    "date",
                    ""
                )
            )[:10]


            same_day = []


            for item in valid_steps:

                item_date = str(
                    item.get(
                        "date",
                        ""
                    )
                )[:10]


                if item_date == latest_date:

                    qty = to_float(
                        item.get(
                            "qty"
                        )
                    )


                    if qty is not None:

                        same_day.append(
                            qty
                        )


            total_steps = (
                sum(
                    same_day
                )
                if same_day
                else None
            )


            parsed[
                "steps"
            ] = {
                "count":
                    int(
                        round(
                            total_steps
                        )
                    )
                    if total_steps is not None
                    else None,

                "date":
                    latest_date
                    or None
            }


    # -------------------------
    # 睡眠
    # -------------------------

    sleep_metric = metrics.get(
        "sleep_analysis"
    )


    if isinstance(
        sleep_metric,
        dict
    ):

        latest_sleep = latest_metric_item(
            sleep_metric.get(
                "data",
                []
            )
        )


        if latest_sleep:

            total_hours = (
                latest_sleep.get(
                    "totalSleep"
                )
                if latest_sleep.get(
                    "totalSleep"
                ) is not None
                else latest_sleep.get(
                    "asleep"
                )
            )


            parsed[
                "sleep"
            ] = {
                "status":
                    "ok",

                "date":
                    latest_sleep.get(
                        "date"
                    ),

                "total_minutes":
                    hours_to_minutes(
                        total_hours
                    ),

                "deep_minutes":
                    hours_to_minutes(
                        latest_sleep.get(
                            "deep"
                        )
                    ),

                "light_minutes":
                    hours_to_minutes(
                        latest_sleep.get(
                            "core"
                        )
                    ),

                "rem_minutes":
                    hours_to_minutes(
                        latest_sleep.get(
                            "rem"
                        )
                    ),

                "awake_minutes":
                    hours_to_minutes(
                        latest_sleep.get(
                            "awake"
                        )
                    ),

                "sleep_start":
                    latest_sleep.get(
                        "sleepStart"
                    ),

                "sleep_end":
                    latest_sleep.get(
                        "sleepEnd"
                    )
            }


    # -------------------------
    # 身体测量
    # -------------------------

    body_measurements = {}


    weight_metric = metric_by_names(
        metrics,
        [
            "weight_&_body_mass",
            "weight_body_mass",
            "body_mass",
            "weight"
        ]
    )

    weight = latest_simple_measurement(
        weight_metric
    )

    if weight:

        body_measurements[
            "weight"
        ] = weight


    body_fat_metric = metric_by_names(
        metrics,
        [
            "body_fat_percentage",
            "body_fat"
        ]
    )

    body_fat = latest_simple_measurement(
        body_fat_metric,
        percent=True
    )

    if body_fat:

        body_measurements[
            "body_fat_percentage"
        ] = body_fat


    lean_body_mass_metric = metric_by_names(
        metrics,
        [
            "lean_body_mass",
            "lean_mass"
        ]
    )

    lean_body_mass = latest_simple_measurement(
        lean_body_mass_metric
    )

    if lean_body_mass:

        body_measurements[
            "lean_body_mass"
        ] = lean_body_mass


    height_metric = metric_by_names(
        metrics,
        [
            "height"
        ]
    )

    height = latest_simple_measurement(
        height_metric
    )

    if height:

        body_measurements[
            "height"
        ] = height


    if body_measurements:

        parsed[
            "body_measurements"
        ] = body_measurements


    return parsed


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
    # Health Auto Export 可添加自定义 Header：
    #
    #   Authorization: Bearer 你的 AUTH_TOKEN
    #
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


    # Health Auto Export JSON V2 可能是：
    # 1. 健康指标：data.metrics[]
    # 2. 月经周期跟踪：data.cycleTracking[]
    #
    # 两种自动化都可以 POST 到同一个 /health/upload。
    normalized = {}


    health_normalized = (
        parse_health_auto_export(
            payload
        )
    )


    if isinstance(
        health_normalized,
        dict
    ):

        normalized = merge_dict(
            normalized,
            health_normalized
        )


    period_tracking = (
        parse_cycle_tracking(
            payload,
            current.get(
                "period_tracking"
            )
        )
    )


    if isinstance(
        period_tracking,
        dict
    ):

        normalized[
            "period_tracking"
        ] = period_tracking

        normalized[
            "source"
        ] = (
            "health_auto_export"
        )


    if not normalized:

        if isinstance(
            payload.get(
                "data"
            ),
            dict
        ):

            normalized = {
                "source":
                    "health_auto_export"
            }

        else:

            normalized = payload


    merged = merge_dict(
        current,
        normalized
    )


    merged["status"] = "ok"

    merged["received_at"] = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )


    if not merged.get(
        "source"
    ):

        merged["source"] = (
            "apple_health"
        )


    save_health_data(
        merged
    )


    return {
        "ok":
            True,

        "source":
            merged.get(
                "source"
            ),

        "received_at":
            merged[
                "received_at"
            ],

        "parsed":
            {
                "heart_rate":
                    "heart_rate"
                    in normalized,

                "oxygen":
                    "oxygen"
                    in normalized,

                "steps":
                    "steps"
                    in normalized,

                "sleep":
                    "sleep"
                    in normalized,

                "body_measurements":
                    "body_measurements"
                    in normalized,

                "period_tracking":
                    "period_tracking"
                    in normalized
            }
    }


# =========================
# 标准 Health MCP 工具
# =========================

@health_mcp.tool()
def get_latest_health() -> dict[str, Any]:
    """
    查询最近一次已经同步到服务器的健康数据总览。
    当用户询问自己最近的心率、静息心率、血氧、步数、睡眠，
    或体重、体脂百分比、去脂体重、身高等身体测量，
    或希望查看整体健康记录时使用。
    数据来自穿戴设备/健康 App，仅用于查看记录，不代表医学诊断。
    只报告工具返回的数据、记录时间和是否缺失。
    不要仅凭这些数值自行标记“正常/异常/优秀/偏低”，
    不要推断用户情绪、身体状态或是否佩戴了手环。
    no_data 只表示当前没有可用记录。
    """

    data = read_health_data()

    general_data = dict(
        data
    )

    general_data.pop(
        "period_tracking",
        None
    )


    return {
        **general_data,
        "interpretation_policy": (
            "只报告记录本身及时间。"
            "不要仅凭这些数值自行判断正常/异常/优秀/偏低，"
            "不要据此推断情绪、身体状态或是否佩戴手环。"
            "对体重、体脂、去脂体重和身高只报告记录，"
            "不要评价身材、胖瘦或理想体重，也不要提供减重或减脂建议。"
            "经期数据只有在用户明确询问时才调用 get_period。"
            "no_data 只表示当前没有可用记录。"
        )
    }


@health_mcp.tool()
def get_heart_rate() -> dict[str, Any]:
    """
    查询最近同步的心率记录，包括最近心率、静息心率、
    最低心率、最高心率和记录时间。
    当用户询问心率、脉搏或静息心率时使用。
    只报告记录值和记录时间，不自行判断正常/异常，
    不根据心率推断情绪或身体状态。
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
    只报告步数和日期，不自行把步数评价为足够、不足、偏低或优秀。
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
    只报告血氧记录值和时间，不自行标记正常/异常/优秀。
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
    如果当前没有近期睡眠数据，会返回 no_data。
    no_data 只表示没有可用的手环/同步记录，
    不能据此判断用户没有睡觉，也不能判断用户是否佩戴了手环。
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
def get_body_measurements() -> dict[str, Any]:
    """
    查询最近同步的身体测量记录，包括体重、体脂百分比、
    去脂体重和身高，以及每项记录的单位和时间。
    当用户询问自己的体重、脂肪率/体脂率、去脂体重、瘦体重或身高时使用。
    只报告工具实际返回的记录值、单位和时间。
    不评价身材、胖瘦、外形或理想体重，
    也不要基于这些记录提供减重、减脂或限制饮食建议。
    """

    data = read_health_data()

    body_measurements = data.get(
        "body_measurements",
        {}
    )


    if not isinstance(
        body_measurements,
        dict
    ):

        body_measurements = {}


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

        "body_measurements":
            body_measurements,

        "important_note": (
            "只报告记录值、单位和时间。"
            "不要评价身材、胖瘦、外形或理想体重，"
            "也不要基于这些记录提供减重或减脂建议。"
        )
    }


@health_mcp.tool()
def get_period() -> dict[str, Any]:
    """
    查询月经周期记录和基于历史记录生成的预测。
    当用户明确询问经期、月经记录、预测经期、排卵日或排卵期时使用。

    返回内容只包括：
    - 实际记录：最近一次经期、最近几次经期开始日期、每日经量记录
    - 预测：预测经期开始/结束、预测排卵日、预测排卵期

    预测优先根据已记录经期历史进行日历估算。
    如果真实历史不足两个周期，服务器可能使用管理员配置的
    典型周期长度与典型经期长度进行备用日历预测。
    不把预测写成已经发生的事实，不确认实际排卵，
    不用于诊断或避孕判断。
    """

    data = read_health_data()

    period_tracking = data.get(
        "period_tracking",
        {}
    )


    if not isinstance(
        period_tracking,
        dict
    ):

        period_tracking = {
            "status":
                "no_data"
        }


    return {
        "status":
            period_tracking.get(
                "status",
                "no_data"
            ),

        "received_at":
            data.get(
                "received_at"
            ),

        "period_tracking":
            period_tracking,

        "important_note": (
            "实际经期记录与预测必须分开表达。"
            "预测优先基于真实经期历史；"
            "历史不足时可能使用服务器配置的典型周期参数进行备用推算。"
            "预测经期、预测排卵日和预测排卵期"
            "都不是实际发生记录，不能确认实际排卵，"
            "也不用于诊断或避孕判断。"
        )
    }


@health_mcp.tool()
def get_health_summary() -> dict[str, Any]:
    """
    获取适合 AI 总结的最近健康数据摘要。
    当用户询问“看看我今天/最近的身体数据”“帮我总结健康记录”
    或同时涉及心率、血氧、步数、睡眠、身体测量中的多项数据时使用。
    总结时只描述记录值、时间和缺失情况。
    不仅凭这些数据自行给出“正常/异常/优秀/偏低”等医学或状态判断。
    经期数据不在普通健康摘要中自动展示；
    用户明确询问经期、月经、预测经期、排卵日或排卵期时使用 get_period。
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

        "body_measurements":
            data.get(
                "body_measurements",
                {}
            ),

        "note": (
            "这些是穿戴设备/健康 App 的记录。"
            "只描述记录值、时间和缺失情况；"
            "不要仅凭这些数值自行判断正常/异常/优秀/偏低，"
            "也不要据此推断情绪、身体状态或是否佩戴手环。"
            "对体重、体脂、去脂体重和身高只报告记录，"
            "不要评价身材、胖瘦或理想体重，也不要提供减重或减脂建议。"
            "经期数据不在普通健康摘要中自动展示；"
            "用户明确询问时应调用 get_period。"
            "no_data 只表示当前没有可用记录。"
        )
    }



# =========================
# 五子棋游戏桥接层
# =========================
#
# /mcp 只暴露下面的五子棋工具给 Echoes 角色。
# /health/mcp 继续由原来的 Health MCP 独立提供，不受这里影响。
#
# 棋局状态保存在 Railway Volume 的 /data 中，
# 因此普通重启/重新部署后仍可保留当前棋局。
#

GAME_DATA_FILE = Path(
    os.environ.get(
        "GAME_DATA_FILE",
        "/data/echoes_gomoku_games.json"
    )
)

GAME_LOCK = threading.Lock()
BOARD_SIZE = 15
COL_LABELS = "ABCDEFGHIJKLMNO"


def _new_game_state():

    return {
        "board": [
            [
                None
                for _ in range(BOARD_SIZE)
            ]
            for _ in range(BOARD_SIZE)
        ],
        "turn": "user",
        "winner": None,
        "game_over": False,
        "move_count": 0,
        "last_move": None,
        "events": [],
        "next_event_id": 1,
        "last_message": None
    }


def _load_game_db():

    if not GAME_DATA_FILE.exists():
        return {}

    try:
        with GAME_DATA_FILE.open(
            "r",
            encoding="utf-8"
        ) as fp:
            data = json.load(fp)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return {}


def _save_game_db(db):

    GAME_DATA_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with GAME_DATA_FILE.open(
        "w",
        encoding="utf-8"
    ) as fp:
        json.dump(
            db,
            fp,
            ensure_ascii=False,
            indent=2
        )


def _add_game_event(
    state,
    event_type,
    actor,
    data
):

    event = {
        "id": state.get(
            "next_event_id",
            1
        ),
        "type": event_type,
        "actor": actor,
        "data": data,
        "time": datetime.now(
            timezone.utc
        ).isoformat()
    }

    state.setdefault(
        "events",
        []
    ).append(event)

    state["next_event_id"] = (
        event["id"] + 1
    )

    return event


def _ensure_game(
    db,
    game_id
):

    state = db.get(game_id)

    if not isinstance(
        state,
        dict
    ):
        state = _new_game_state()
        db[game_id] = state

        _add_game_event(
            state,
            "game_started",
            "system",
            {
                "message": "五子棋开始",
                "first_turn": "user"
            }
        )

    return state


def _coordinate_label(
    row,
    col
):

    return (
        f"{COL_LABELS[col]}"
        f"{row + 1}"
    )


def _check_gomoku_win(
    board,
    row,
    col,
    player
):

    directions = [
        (1, 0),
        (0, 1),
        (1, 1),
        (1, -1)
    ]

    for dr, dc in directions:
        count = 1

        r = row + dr
        c = col + dc

        while (
            0 <= r < BOARD_SIZE
            and 0 <= c < BOARD_SIZE
            and board[r][c] == player
        ):
            count += 1
            r += dr
            c += dc

        r = row - dr
        c = col - dc

        while (
            0 <= r < BOARD_SIZE
            and 0 <= c < BOARD_SIZE
            and board[r][c] == player
        ):
            count += 1
            r -= dr
            c -= dc

        if count >= 5:
            return True

    return False


def _public_game_state(
    game_id,
    state
):

    stones = []

    board = state.get(
        "board",
        []
    )

    for row in range(
        min(
            len(board),
            BOARD_SIZE
        )
    ):
        row_items = board[row]

        if not isinstance(
            row_items,
            list
        ):
            continue

        for col in range(
            min(
                len(row_items),
                BOARD_SIZE
            )
        ):
            player = row_items[col]

            if player in {
                "user",
                "sei"
            }:
                stones.append(
                    {
                        "player": player,
                        "row": row,
                        "col": col,
                        "coordinate":
                            _coordinate_label(
                                row,
                                col
                            )
                    }
                )

    return {
        "game_id": game_id,
        "game": "gomoku",
        "board_size": BOARD_SIZE,
        "user_color": "black",
        "sei_color": "white",
        "turn": state.get("turn"),
        "winner": state.get("winner"),
        "game_over": bool(
            state.get("game_over")
        ),
        "move_count": state.get(
            "move_count",
            0
        ),
        "last_move": state.get(
            "last_move"
        ),
        "last_message": state.get(
            "last_message"
        ),
        "stones": stones
    }


def _make_gomoku_move(
    game_id,
    row,
    col,
    player
):

    try:
        row = int(row)
        col = int(col)
    except Exception:
        raise ValueError(
            "row 和 col 必须是整数。"
        )

    if player not in {
        "user",
        "sei"
    }:
        raise ValueError(
            "未知玩家。"
        )

    if not (
        0 <= row < BOARD_SIZE
        and
        0 <= col < BOARD_SIZE
    ):
        raise ValueError(
            "row 和 col 必须在 0 到 14 之间。"
        )

    with GAME_LOCK:
        db = _load_game_db()
        state = _ensure_game(
            db,
            game_id
        )

        if state.get(
            "game_over"
        ):
            raise ValueError(
                "这一局已经结束。"
            )

        if state.get(
            "turn"
        ) != player:
            raise ValueError(
                "现在轮到 "
                f"{state.get('turn')}，"
                f"不是 {player}。"
            )

        board = state.get(
            "board"
        )

        if not isinstance(
            board,
            list
        ):
            raise ValueError(
                "棋盘数据损坏，请重新开始。"
            )

        if board[row][col] is not None:
            raise ValueError(
                f"{_coordinate_label(row, col)} "
                "已经有棋子。"
            )

        board[row][col] = player
        state["move_count"] = (
            int(
                state.get(
                    "move_count",
                    0
                )
            )
            + 1
        )

        move = {
            "player": player,
            "row": row,
            "col": col,
            "coordinate":
                _coordinate_label(
                    row,
                    col
                ),
            "move_number":
                state["move_count"]
        }

        state["last_move"] = move

        _add_game_event(
            state,
            "move",
            player,
            move
        )

        if _check_gomoku_win(
            board,
            row,
            col,
            player
        ):
            state["winner"] = player
            state["game_over"] = True
            state["turn"] = None

            _add_game_event(
                state,
                "game_over",
                "system",
                {
                    "winner": player,
                    "reason":
                        "five_in_a_row"
                }
            )

        elif (
            state["move_count"]
            >= BOARD_SIZE * BOARD_SIZE
        ):
            state["winner"] = "draw"
            state["game_over"] = True
            state["turn"] = None

            _add_game_event(
                state,
                "game_over",
                "system",
                {
                    "winner": "draw",
                    "reason": "board_full"
                }
            )

        else:
            state["turn"] = (
                "sei"
                if player == "user"
                else "user"
            )

        db[game_id] = state
        _save_game_db(db)

        return {
            "ok": True,
            "move": move,
            "state": _public_game_state(
                game_id,
                state
            )
        }


def get_game_state(
    game_id: str = "main"
):
    """
    查看当前五子棋棋局。
    用户执黑先手，Sei 执白。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    with GAME_LOCK:
        db = _load_game_db()
        existed = game_id in db
        state = _ensure_game(
            db,
            game_id
        )

        if not existed:
            _save_game_db(db)

        return _public_game_state(
            game_id,
            state
        )


def get_game_state_mcp(
    game_id: str = "main"
):
    """
    仅供 MCP 使用的紧凑棋局状态。
    HTML 仍然使用原来的完整 get_game_state，不受影响。
    B=用户黑棋，W=Sei 白棋。
    """

    state = get_game_state(
        game_id
    )

    compact_stones = []

    for stone in state.get(
        "stones",
        []
    ):
        side = (
            "B"
            if stone.get("player") == "user"
            else "W"
        )

        compact_stones.append(
            f"{stone.get('coordinate')}:{side}"
        )

    last_move = state.get(
        "last_move"
    )

    last_text = "-"

    if isinstance(
        last_move,
        dict
    ):
        last_side = (
            "B"
            if last_move.get("player") == "user"
            else "W"
        )

        last_text = (
            f"{last_move.get('coordinate')}:"
            f"{last_side}"
        )

    winner = state.get(
        "winner"
    )

    if winner == "user":
        winner_text = "B"
    elif winner == "sei":
        winner_text = "W"
    elif winner == "draw":
        winner_text = "draw"
    else:
        winner_text = "-"

    return (
        f"turn={state.get('turn') or '-'};"
        f"last={last_text};"
        f"stones={','.join(compact_stones) or '-'};"
        f"winner={winner_text};"
        f"over={1 if state.get('game_over') else 0}"
    )


def play_gomoku_move(
    row: int,
    col: int,
    game_id: str = "main"
):
    """
    让 Sei 在当前棋局中亲自下一颗白棋。
    只有 turn=sei 时才能成功。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    try:
        return _make_gomoku_move(
            game_id,
            row,
            col,
            "sei"
        )

    except ValueError as error:
        return {
            "ok": False,
            "error": str(error),
            "state": get_game_state(
                game_id
            )
        }


def get_game_events(
    game_id: str = "main",
    since_event_id: int = 0
):
    """
    查看棋局事件；可只读取某个事件编号之后的新变化。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    try:
        since_event_id = int(
            since_event_id
        )
    except Exception:
        since_event_id = 0

    with GAME_LOCK:
        db = _load_game_db()
        existed = game_id in db
        state = _ensure_game(
            db,
            game_id
        )

        if not existed:
            _save_game_db(db)

        events = [
            event
            for event in state.get(
                "events",
                []
            )
            if int(
                event.get(
                    "id",
                    0
                )
            ) > since_event_id
        ]

        return {
            "game_id": game_id,
            "events": events,
            "latest_event_id": (
                int(
                    state.get(
                        "next_event_id",
                        1
                    )
                )
                - 1
            )
        }


def game_say(
    message: str,
    game_id: str = "main"
):
    """
    让 Sei 把一句游戏中的话写进棋局，供 HTML 显示。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    message = str(
        message
        or ""
    ).strip()

    if not message:
        return {
            "ok": False,
            "error": "message 不能为空。"
        }

    if len(message) > 500:
        message = message[:500]

    with GAME_LOCK:
        db = _load_game_db()
        state = _ensure_game(
            db,
            game_id
        )

        item = {
            "actor": "sei",
            "message": message,
            "time": datetime.now(
                timezone.utc
            ).isoformat()
        }

        state["last_message"] = item

        event = _add_game_event(
            state,
            "message",
            "sei",
            {
                "message": message
            }
        )

        db[game_id] = state
        _save_game_db(db)

        return {
            "ok": True,
            "message": item,
            "event_id": event["id"]
        }



def play_gomoku_turn(
    row: int,
    col: int,
    message: str,
    game_id: str = "main"
):
    """
    让 Sei 在一次 MCP 调用中完成一整个回合：
    先落一颗白棋，再把要对用户说的话写进棋局。
    这样 Echoes 只需要为本回合确认一次工具调用。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    message = str(
        message
        or ""
    ).strip()

    if not message:
        return {
            "ok": False,
            "error": "message 不能为空。",
            "state": get_game_state(
                game_id
            )
        }

    if len(message) > 500:
        message = message[:500]

    try:
        move_result = _make_gomoku_move(
            game_id,
            row,
            col,
            "sei"
        )

    except ValueError as error:
        return {
            "ok": False,
            "error": str(error),
            "state": get_game_state(
                game_id
            )
        }

    say_result = game_say(
        message=message,
        game_id=game_id
    )

    final_state = get_game_state(
        game_id
    )

    move = move_result.get(
        "move"
    ) or {}

    winner = final_state.get(
        "winner"
    )

    if winner == "user":
        winner_text = "B"
    elif winner == "sei":
        winner_text = "W"
    elif winner == "draw":
        winner_text = "draw"
    else:
        winner_text = "-"

    return (
        "ok=1;"
        f"move={move.get('coordinate', '-')}:W;"
        f"next={final_state.get('turn') or '-'};"
        f"winner={winner_text};"
        f"over={1 if final_state.get('game_over') else 0}"
    )

def reset_gomoku(
    game_id: str = "main"
):
    """
    重新开始一局五子棋；用户继续执黑先手。
    """

    game_id = str(
        game_id
        or "main"
    ).strip() or "main"

    with GAME_LOCK:
        db = _load_game_db()
        state = _new_game_state()

        _add_game_event(
            state,
            "game_started",
            "system",
            {
                "message":
                    "五子棋重新开始",
                "first_turn": "user"
            }
        )

        db[game_id] = state
        _save_game_db(db)

        return {
            "ok": True,
            "state": _public_game_state(
                game_id,
                state
            )
        }


# =========================
# 五子棋普通 HTTP 接口
# HTML 通过这些接口同步棋盘
# =========================

@app.get(
    "/game/gomoku/{game_id}"
)
def gomoku_http_state(
    game_id: str
):

    return get_game_state(
        game_id
    )


@app.post(
    "/game/gomoku/{game_id}/user-move"
)
async def gomoku_http_user_move(
    game_id: str,
    request: Request
):

    try:
        body = await request.json()

        result = _make_gomoku_move(
            game_id,
            body.get("row"),
            body.get("col"),
            "user"
        )

        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


@app.post(
    "/game/gomoku/{game_id}/reset"
)
def gomoku_http_reset(
    game_id: str
):

    return reset_gomoku(
        game_id
    )


# =========================
# MCP 工具定义
# =========================

TOOLS = [
    {
        "name": "get_game_state",
        "description": (
            "读取当前15×15五子棋。B=用户黑棋，W=Sei白棋。"
            "返回turn、last、stones、winner。下棋前先调用。"
        ),
        "annotations": {
            "title": "查看五子棋棋局",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "description": "棋局编号。默认 main。",
                    "default": "main"
                }
            }
        }
    },
    {
        "name": "play_gomoku_turn",
        "description": (
            "Sei完成一回合：落一颗白棋并写一句游戏台词。"
            "row/col范围0~14，H8=row7,col7。先读取棋局后调用。"
        ),
        "annotations": {
            "title": "Sei 完成五子棋回合",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 14,
                    "description": "Sei 要落子的行号，0~14"
                },
                "col": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 14,
                    "description": "Sei 要落子的列号，0~14"
                },
                "message": {
                    "type": "string",
                    "description": "Sei 本回合想对用户说的一句自然的话"
                },
                "game_id": {
                    "type": "string",
                    "description": "棋局编号，默认 main",
                    "default": "main"
                }
            },
            "required": [
                "row",
                "col",
                "message"
            ]
        }
    },
    {
        "name": "get_game_events",
        "description": (
            "查看五子棋最近发生的事件。"
            "适合在聊天里追问用户刚才下在哪里、游戏是否结束，"
            "或检查自某个事件编号之后发生了什么。"
            "正常对弈时如果用户消息已经附带 HTML 生成的当前棋盘状态，"
            "不需要额外调用本工具。"
        ),
        "annotations": {
            "title": "查看五子棋事件",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "default": "main"
                },
                "since_event_id": {
                    "type": "integer",
                    "description": "只返回这个事件编号之后的新事件。",
                    "default": 0
                }
            }
        }
    },
    {
        "name": "reset_gomoku",
        "description": (
            "重新开始一局五子棋。"
            "会清空当前棋盘，用户仍然执黑先手。"
        ),
        "annotations": {
            "title": "重新开始五子棋",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {
                    "type": "string",
                    "default": "main"
                }
            }
        }
    }
]


FUNCTIONS = {
    "get_game_state":
        get_game_state_mcp,

    "play_gomoku_turn":
        play_gomoku_turn,

    "get_game_events":
        get_game_events,

    "reset_gomoku":
        reset_gomoku,
}


# =========================
# Railway 状态接口
# =========================
@app.get("/")
def home():

    return {
        "status": "online",
        "server": "echoes-game-mcp",
        "game": "gomoku",
        "game_api": "/game/gomoku/main",
        "health_mcp": "/health/mcp",
        "tools": [
            "get_game_state",
            "play_gomoku_turn",
            "get_game_events",
            "reset_gomoku"
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
                        "echoes-game-mcp",

                    "version":
                        "2.0"
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

                        "text": (
                            json.dumps(
                                result,
                                ensure_ascii=False
                            )
                            if isinstance(
                                result,
                                (dict, list)
                            )
                            else str(result)
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
# SiliconFlow GLM-5.2 低推理代理
# =========================
#
# Echoes 的 API Endpoint 可填写：
#   https://你的-Railway-域名/siliconflow/v1
#
# 代理不会保存 API Key；它只把 Echoes 发来的 Authorization
# 原样转发给 SiliconFlow。
#
# 仅当模型名为 zai-org/GLM-5.2 时，自动把 thinking_budget
# 限制为 GLM_THINKING_BUDGET（默认 2048）。
# 其他模型请求原样转发，不强行修改。
#
# 如果以后想改预算，不必改代码，只需在 Railway Variables 里设置：
#   GLM_THINKING_BUDGET=3072
# 或其他正整数。
#

SILICONFLOW_UPSTREAM = (
    os.environ.get(
        "SILICONFLOW_UPSTREAM",
        "https://api.siliconflow.cn/v1"
    )
    .strip()
    .rstrip("/")
)


def _read_thinking_budget():

    raw = os.environ.get(
        "GLM_THINKING_BUDGET",
        "2048"
    ).strip()

    try:
        value = int(raw)
    except Exception:
        value = 2048

    # SiliconFlow 文档给出的 thinking_budget 有有效范围；
    # 这里做一个保守的保护，避免误填 0 或极端数值。
    if value < 128:
        value = 128

    if value > 32768:
        value = 32768

    return value


def _should_limit_thinking(payload):

    if not isinstance(
        payload,
        dict
    ):
        return False

    model = str(
        payload.get(
            "model",
            ""
        )
    ).strip().lower()

    return model in {
        "zai-org/glm-5.2",
        "pro/zai-org/glm-5.2",
    }


def _proxy_http_call(
    method,
    upstream_url,
    request_headers,
    body_bytes
):

    upstream_request = URLRequest(
        upstream_url,
        data=(
            body_bytes
            if method != "GET"
            else None
        ),
        method=method,
    )

    # 只转发真正需要的头，避免 Host / Content-Length 等反向代理头造成冲突。
    for header_name in (
        "Authorization",
        "Content-Type",
        "Accept",
        "User-Agent",
    ):

        value = request_headers.get(
            header_name
        )

        if value:
            upstream_request.add_header(
                header_name,
                value
            )

    try:

        with urlopen(
            upstream_request,
            timeout=180
        ) as upstream_response:

            return (
                upstream_response.status,
                upstream_response.headers.get(
                    "Content-Type",
                    "application/json"
                ),
                upstream_response.read(),
            )

    except HTTPError as exc:

        return (
            exc.code,
            exc.headers.get(
                "Content-Type",
                "application/json"
            ) if exc.headers else "application/json",
            exc.read(),
        )


@app.get(
    "/siliconflow-proxy/status"
)
def siliconflow_proxy_status():

    return {
        "status": "online",
        "upstream": SILICONFLOW_UPSTREAM,
        "endpoint": "/siliconflow/v1",
        "model": "zai-org/GLM-5.2",
        "thinking_budget": _read_thinking_budget(),
        "api_key_storage": "not_stored",
    }


@app.api_route(
    "/siliconflow/v1/{subpath:path}",
    methods=[
        "GET",
        "POST"
    ]
)
async def siliconflow_proxy(
    subpath: str,
    request: Request
):

    method = request.method.upper()

    query_string = request.url.query

    upstream_url = (
        f"{SILICONFLOW_UPSTREAM}/"
        f"{subpath.lstrip('/')}"
    )

    if query_string:
        upstream_url += (
            "?" + query_string
        )

    original_body = await request.body()
    body_bytes = original_body
    applied_budget = None

    is_chat_completions = (
        method == "POST"
        and subpath.strip("/") == "chat/completions"
    )

    if (
        is_chat_completions
        and original_body
    ):

        try:
            payload = json.loads(
                original_body.decode(
                    "utf-8"
                )
            )
        except Exception:
            payload = None

        if _should_limit_thinking(
            payload
        ):

            applied_budget = (
                _read_thinking_budget()
            )

            # 无论 Echoes 有没有传 thinking_budget，
            # 对 GLM-5.2 都以 Railway 上的预算为准。
            payload[
                "thinking_budget"
            ] = applied_budget

            body_bytes = json.dumps(
                payload,
                ensure_ascii=False
            ).encode(
                "utf-8"
            )

    wants_stream = (
        isinstance(payload, dict)
        and payload.get("stream") is True
    ) if is_chat_completions else False

    # Echoes 的工具调用会使用 OpenAI 风格 SSE 流。
    # 必须逐块透传，不能等 SiliconFlow 整个响应结束后再一次性返回，
    # 否则 Echoes 会一直停在“正在处理外部任务”。
    if wants_stream:

        forward_headers = {}

        for header_name in (
            "Authorization",
            "Content-Type",
            "Accept",
            "User-Agent",
        ):

            value = request.headers.get(
                header_name
            )

            if value:
                forward_headers[
                    header_name
                ] = value

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=180.0,
                write=30.0,
                pool=30.0,
            )
        )

        try:
            upstream_request = client.build_request(
                method,
                upstream_url,
                headers=forward_headers,
                content=body_bytes,
            )

            upstream_response = await client.send(
                upstream_request,
                stream=True,
            )

        except Exception as exc:
            await client.aclose()

            return Response(
                content=json.dumps(
                    {
                        "error": {
                            "message": (
                                "SiliconFlow upstream unavailable: "
                                f"{exc}"
                            )
                        }
                    },
                    ensure_ascii=False
                ),
                status_code=502,
                media_type="application/json",
            )

        content_type = upstream_response.headers.get(
            "Content-Type",
            "text/event-stream"
        )

        async def stream_body():

            try:
                async for chunk in upstream_response.aiter_bytes():
                    if chunk:
                        yield chunk

            finally:
                await upstream_response.aclose()
                await client.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream_response.status_code,
            media_type=content_type.split(
                ";",
                1
            )[0].strip(),
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Echoes-GLM-Thinking-Budget": (
                    str(applied_budget)
                    if applied_budget is not None
                    else "unchanged"
                ),
            },
        )

    try:

        (
            status_code,
            content_type,
            response_body
        ) = await asyncio.to_thread(
            _proxy_http_call,
            method,
            upstream_url,
            request.headers,
            body_bytes,
        )

    except URLError as exc:

        return Response(
            content=json.dumps(
                {
                    "error": {
                        "message": (
                            "SiliconFlow upstream unavailable: "
                            f"{exc}"
                        )
                    }
                },
                ensure_ascii=False
            ),
            status_code=502,
            media_type="application/json",
        )

    # 非流式 JSON 响应时，把 usage 打到 Railway Logs。
    # 不改变返回给 Echoes 的内容。
    if (
        is_chat_completions
        and "application/json" in content_type.lower()
        and response_body
    ):

        try:
            response_json = json.loads(
                response_body.decode(
                    "utf-8"
                )
            )

            usage = response_json.get(
                "usage"
            ) or {}

            print(
                "[GLM low-thinking proxy]",
                f"budget={applied_budget}",
                f"prompt_tokens={usage.get('prompt_tokens')}",
                f"completion_tokens={usage.get('completion_tokens')}",
                f"total_tokens={usage.get('total_tokens')}",
                f"prompt_details={usage.get('prompt_tokens_details')}",
                f"completion_details={usage.get('completion_tokens_details')}",
                flush=True,
            )

        except Exception:
            pass

    response_headers = {
        "X-Echoes-GLM-Thinking-Budget": (
            str(applied_budget)
            if applied_budget is not None
            else "unchanged"
        )
    }

    # 非流式响应保留上游 Content-Type。
    # stream=true 已在上方通过 StreamingResponse 实时透传。
    return Response(
        content=response_body,
        status_code=status_code,
        media_type=content_type.split(
            ";",
            1
        )[0].strip(),
        headers=response_headers,
    )


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
