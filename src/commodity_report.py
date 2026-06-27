#!/usr/bin/env python3
"""
TIPS + precious metals macro report generator.

The script intentionally uses only Python standard library modules so the BAT file
can run on a plain Windows Python install.
"""

from __future__ import annotations

import argparse
import csv
import html
import http.cookiejar
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
DATA_DIR = REPORTS_DIR / "data"
MD_DIR = REPORTS_DIR / "markdown"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


@dataclass
class SeriesPoint:
    date: str
    value: float


def log(message: str) -> None:
    print(message, flush=True)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)


def fetch_text(url: str, *, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str, *, timeout: int = 25) -> dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout))


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and value.strip() in {"", "."}:
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def pct_change(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous is None or previous == 0:
        return None
    return (latest / previous - 1) * 100


def fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}{suffix}"


def latest_point(points: list[SeriesPoint]) -> SeriesPoint | None:
    return points[-1] if points else None


def point_n_back(points: list[SeriesPoint], n: int) -> SeriesPoint | None:
    if len(points) <= n:
        return None
    return points[-1 - n]


def collect_fred_series(series_ids: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sid, name in series_ids.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(sid)}"
        try:
            text = fetch_text(url, timeout=8)
            rows = csv.DictReader(text.splitlines())
            points: list[SeriesPoint] = []
            for row in rows:
                value = safe_float(row.get(sid))
                date = row.get("observation_date")
                if date and value is not None:
                    points.append(SeriesPoint(date=date, value=value))
            latest = latest_point(points)
            out[sid] = {
                "name": name,
                "source": "FRED",
                "points": [p.__dict__ for p in points[-520:]],
                "latest": latest.__dict__ if latest else None,
                "change_1w": None if not latest else latest.value - (point_n_back(points, 5).value if point_n_back(points, 5) else latest.value),
                "change_1m": None if not latest else latest.value - (point_n_back(points, 21).value if point_n_back(points, 21) else latest.value),
            }
            log(f"  OK FRED: {name}")
        except Exception as exc:
            out[sid] = {"name": name, "source": "FRED", "error": str(exc)}
            log(f"  FAIL FRED: {name} - {exc}")
    return out


def extract_html_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        cells: list[str] = []
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I):
            clean = re.sub(r"<[^>]+>", " ", cell)
            clean = html.unescape(re.sub(r"\s+", " ", clean)).strip()
            cells.append(clean)
        if cells:
            rows.append(cells)
    return rows


def fetch_treasury_table(table_type: str) -> list[dict[str, str]]:
    current_year = datetime.now().year
    records: list[dict[str, str]] = []
    for year in [current_year - 1, current_year]:
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView"
            f"?type={urllib.parse.quote(table_type)}&field_tdr_date_value={year}"
        )
        text = fetch_text(url, timeout=30)
        rows = extract_html_table_rows(text)
        if len(rows) < 2:
            continue
        header = rows[0]
        for row in rows[1:]:
            if len(row) != len(header):
                continue
            records.append(dict(zip(header, row)))
    records.sort(key=lambda item: datetime.strptime(item["Date"], "%m/%d/%Y"))
    return records


def treasury_points(records: list[dict[str, str]], column: str) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for row in records:
        value = safe_float(row.get(column))
        if value is None:
            continue
        date = datetime.strptime(row["Date"], "%m/%d/%Y").strftime("%Y-%m-%d")
        points.append(SeriesPoint(date=date, value=value))
    return points


def series_payload(name: str, source: str, points: list[SeriesPoint]) -> dict[str, Any]:
    latest = latest_point(points)
    one_week = point_n_back(points, 5)
    one_month = point_n_back(points, 21)
    return {
        "name": name,
        "source": source,
        "points": [p.__dict__ for p in points[-520:]],
        "latest": latest.__dict__ if latest else None,
        "change_1w": None if not latest or not one_week else latest.value - one_week.value,
        "change_1m": None if not latest or not one_month else latest.value - one_month.value,
    }


def collect_macro_series() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        real_records = fetch_treasury_table("daily_treasury_real_yield_curve")
        out["DFII10"] = series_payload(
            "10年期TIPS实际收益率",
            "U.S. Treasury daily real yield curve",
            treasury_points(real_records, "10 YR"),
        )
        log("  OK Treasury: 10年期TIPS实际收益率")
    except Exception as exc:
        out["DFII10"] = {"name": "10年期TIPS实际收益率", "source": "U.S. Treasury", "error": str(exc)}
        log(f"  FAIL Treasury real yield: {exc}")

    try:
        nominal_records = fetch_treasury_table("daily_treasury_yield_curve")
        out["DGS10"] = series_payload(
            "美国10年期国债收益率",
            "U.S. Treasury daily yield curve",
            treasury_points(nominal_records, "10 Yr"),
        )
        out["DGS2"] = series_payload(
            "美国2年期国债收益率",
            "U.S. Treasury daily yield curve",
            treasury_points(nominal_records, "2 Yr"),
        )
        log("  OK Treasury: 美国2年/10年国债收益率")
    except Exception as exc:
        out.setdefault("DGS10", {"name": "美国10年期国债收益率", "source": "U.S. Treasury", "error": str(exc)})
        out.setdefault("DGS2", {"name": "美国2年期国债收益率", "source": "U.S. Treasury", "error": str(exc)})
        log(f"  FAIL Treasury nominal yield: {exc}")

    return out


def collect_yahoo_chart(symbol: str, name: str, *, interval: str = "1d", range_: str = "2y") -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_}&interval={interval}&includePrePost=false"
    try:
        last_exc: Exception | None = None
        data: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                data = fetch_json(url)
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.8 * (attempt + 1))
        if data is None:
            raise last_exc or RuntimeError("Yahoo chart request failed")
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        points: list[dict[str, Any]] = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            close_v = safe_float(close)
            if close_v is None:
                continue
            points.append(
                {
                    "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "value": close_v,
                    "volume": safe_float(volume),
                }
            )
        latest = points[-1] if points else None
        prev = points[-2] if len(points) > 1 else None
        wk = points[-6] if len(points) > 6 and interval == "1d" else (points[-2] if len(points) > 1 else None)
        mo = points[-22] if len(points) > 22 and interval == "1d" else (points[-5] if len(points) > 5 else None)
        return {
            "symbol": symbol,
            "name": name,
            "source": "Yahoo Finance",
            "points": points,
            "latest": latest,
            "pct_1d": pct_change(latest["value"] if latest else None, prev["value"] if prev else None),
            "pct_1w": pct_change(latest["value"] if latest else None, wk["value"] if wk else None),
            "pct_1m": pct_change(latest["value"] if latest else None, mo["value"] if mo else None),
        }
    except Exception as exc:
        log(f"  FAIL Yahoo: {name} - {exc}")
        return {"symbol": symbol, "name": name, "source": "Yahoo Finance", "error": str(exc), "points": []}


def collect_market_prices() -> dict[str, Any]:
    assets = {
        "GC=F": "COMEX 黄金期货",
        "SI=F": "COMEX 白银期货",
        "CL=F": "WTI 原油",
        "HG=F": "COMEX 铜",
        "^VIX": "VIX 指数",
        "DX-Y.NYB": "美元指数",
        "SPY": "标普500 ETF",
    }
    daily: dict[str, Any] = {}
    weekly: dict[str, Any] = {}
    for symbol, name in assets.items():
        log(f"  Fetch market: {name}")
        daily[symbol] = collect_yahoo_chart(symbol, name, interval="1d", range_="2y")
        time.sleep(0.2)
        if symbol in {"GC=F", "SI=F"}:
            weekly[symbol] = collect_yahoo_chart(symbol, name + "周线", interval="1wk", range_="5y")
            time.sleep(0.2)
    return {"daily": daily, "weekly": weekly}


def option_mid(option: dict[str, Any]) -> float | None:
    bid = safe_float(option.get("bid"))
    ask = safe_float(option.get("ask"))
    last = safe_float(option.get("lastPrice"))
    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2
    return last


def simplify_option(option: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract": option.get("contractSymbol"),
        "strike": safe_float(option.get("strike")),
        "last": safe_float(option.get("lastPrice")),
        "bid": safe_float(option.get("bid")),
        "ask": safe_float(option.get("ask")),
        "mid": option_mid(option),
        "volume": safe_float(option.get("volume")),
        "open_interest": safe_float(option.get("openInterest")),
        "implied_vol": safe_float(option.get("impliedVolatility")),
    }


def choose_expiration(expirations: list[int]) -> int | None:
    if not expirations:
        return None
    now = int(time.time())
    target = now + 45 * 24 * 3600
    future = [x for x in expirations if x > now]
    if not future:
        return expirations[0]
    return min(future, key=lambda x: abs(x - target))


def collect_option_chain(symbol: str, label: str, *, proxy_for: str | None = None) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    opener, crumb = yahoo_authorized_opener()
    crumb_qs = f"?crumb={urllib.parse.quote(crumb)}" if crumb else ""
    base_url = f"https://query2.finance.yahoo.com/v7/finance/options/{encoded}"
    try:
        first = yahoo_fetch_json(opener, base_url + crumb_qs)
        result = first["optionChain"]["result"][0]
        expiration = choose_expiration(result.get("expirationDates", []))
        if expiration:
            sep = "&" if crumb_qs else "?"
            data = yahoo_fetch_json(opener, f"{base_url}{crumb_qs}{sep}date={expiration}")
            result = data["optionChain"]["result"][0]
        quote = result.get("quote", {})
        chain = (result.get("options") or [{}])[0]
        calls = [simplify_option(x) for x in chain.get("calls", [])]
        puts = [simplify_option(x) for x in chain.get("puts", [])]
        underlying = safe_float(quote.get("regularMarketPrice")) or safe_float(quote.get("postMarketPrice"))
        expiry_text = None
        if expiration:
            expiry_text = datetime.fromtimestamp(expiration, tz=timezone.utc).strftime("%Y-%m-%d")

        def nearest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not items or underlying is None:
                return None
            usable = [x for x in items if x.get("strike") is not None]
            return min(usable, key=lambda x: abs(x["strike"] - underlying)) if usable else None

        def top_oi(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
            return sorted(
                [x for x in items if x.get("open_interest") is not None],
                key=lambda x: x.get("open_interest") or 0,
                reverse=True,
            )[:limit]

        call_oi = sum(x.get("open_interest") or 0 for x in calls)
        put_oi = sum(x.get("open_interest") or 0 for x in puts)
        atm_call = nearest(calls)
        atm_put = nearest(puts)
        straddle = None
        if atm_call and atm_put:
            c_mid = atm_call.get("mid")
            p_mid = atm_put.get("mid")
            if c_mid is not None and p_mid is not None:
                straddle = c_mid + p_mid

        return {
            "symbol": symbol,
            "label": label,
            "proxy_for": proxy_for,
            "source": "Yahoo Finance options",
            "underlying": underlying,
            "expiration": expiry_text,
            "put_call_oi_ratio": None if call_oi == 0 else put_oi / call_oi,
            "call_open_interest": call_oi,
            "put_open_interest": put_oi,
            "atm_call": atm_call,
            "atm_put": atm_put,
            "atm_straddle": straddle,
            "implied_move_pct": None if not straddle or not underlying else straddle / underlying * 100,
            "top_calls_by_oi": top_oi(calls),
            "top_puts_by_oi": top_oi(puts),
            "status": "ok",
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "label": label,
            "proxy_for": proxy_for,
            "source": "Yahoo Finance options",
            "status": "error",
            "error": str(exc),
        }


def yahoo_authorized_opener() -> tuple[urllib.request.OpenerDirector, str | None]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    crumb = None
    try:
        warmup = urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": USER_AGENT})
        opener.open(warmup, timeout=20).close()
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            headers={"User-Agent": USER_AGENT},
        )
        with opener.open(req, timeout=20) as resp:
            crumb = resp.read().decode("utf-8", errors="replace").strip()
    except Exception:
        crumb = None
    return opener, crumb


def yahoo_fetch_json(opener: urllib.request.OpenerDirector, url: str, *, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def collect_options() -> dict[str, Any]:
    targets = [
        ("GC=F", "COMEX 黄金期权", None, "GLD", "GLD 黄金ETF期权代理"),
        ("SI=F", "COMEX 白银期权", None, "SLV", "SLV 白银ETF期权代理"),
    ]
    out: dict[str, Any] = {}

    def with_retries(symbol: str, label: str, proxy_for: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for attempt in range(3):
            result = collect_option_chain(symbol, label, proxy_for=proxy_for)
            if result.get("status") == "ok" and result.get("atm_call"):
                return result
            time.sleep(1.0 * (attempt + 1))
        return result

    for primary, primary_label, _, proxy, proxy_label in targets:
        log(f"  Fetch options: {primary_label}")
        result = with_retries(primary, primary_label)
        time.sleep(0.3)
        if result.get("status") != "ok" or not result.get("atm_call"):
            log(f"  Fallback options proxy: {proxy_label}")
            result = with_retries(proxy, proxy_label, proxy_for=primary_label)
        out[primary] = result
        time.sleep(0.3)
    return out


def returns_from_points(points: list[dict[str, Any]]) -> list[float]:
    values = [safe_float(p.get("value")) for p in points]
    values = [x for x in values if x is not None]
    returns: list[float] = []
    for prev, cur in zip(values, values[1:]):
        if prev:
            returns.append((cur / prev - 1) * 100)
    return returns


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 10:
        return None
    x = a[-n:]
    y = b[-n:]
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return None


def rolling_real_rate_signal(tips: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    tips_points = [SeriesPoint(**p) for p in tips.get("points", [])]
    gold_points = gold.get("points", [])
    latest_tips = latest_point(tips_points)
    latest_gold = gold.get("latest", {})
    one_month_tips = point_n_back(tips_points, 21)
    return {
        "tips_latest": latest_tips.value if latest_tips else None,
        "tips_date": latest_tips.date if latest_tips else None,
        "tips_1m_change_bp": None if not latest_tips or not one_month_tips else (latest_tips.value - one_month_tips.value) * 100,
        "gold_latest": latest_gold.get("value"),
        "gold_1m_pct": gold.get("pct_1m"),
        "gold_tips_daily_corr": correlation(
            returns_from_points(gold_points[-260:]),
            [p.value for p in tips_points[-260:]],
        ),
    }


def build_local_summary(data: dict[str, Any]) -> str:
    fred = data["fred"]
    markets = data["markets"]["daily"]
    options = data["options"]
    tips = fred.get("DFII10", {})
    gold = markets.get("GC=F", {})
    silver = markets.get("SI=F", {})
    vix = markets.get("^VIX", {})
    dxy = markets.get("DX-Y.NYB", {})
    signal = data["signals"]

    lines: list[str] = []
    tips_latest = tips.get("latest", {})
    lines.append(
        f"10年期TIPS实际收益率最新为 {fmt(tips_latest.get('value'), 2, '%')} "
        f"({tips_latest.get('date', 'N/A')})，近1个月变化 {fmt(signal.get('tips_1m_change_bp'), 1, 'bp')}。"
    )
    lines.append(
        f"黄金期货最新 {fmt((gold.get('latest') or {}).get('value'), 2)}，"
        f"1周 {fmt(gold.get('pct_1w'), 2, '%')}，1个月 {fmt(gold.get('pct_1m'), 2, '%')}；"
        f"白银期货1个月 {fmt(silver.get('pct_1m'), 2, '%')}。"
    )
    lines.append(
        f"VIX 最新 {fmt((vix.get('latest') or {}).get('value'), 2)}，"
        f"美元指数1个月 {fmt(dxy.get('pct_1m'), 2, '%')}。"
    )

    gold_opt = options.get("GC=F", {})
    silver_opt = options.get("SI=F", {})
    if gold_opt.get("status") == "ok":
        tag = "代理" if gold_opt.get("proxy_for") else "COMEX"
        lines.append(
            f"黄金期权({tag}) Put/Call OI 比为 {fmt(gold_opt.get('put_call_oi_ratio'), 2)}，"
            f"ATM跨式隐含波动区间约 {fmt(gold_opt.get('implied_move_pct'), 2, '%')}。"
        )
    if silver_opt.get("status") == "ok":
        tag = "代理" if silver_opt.get("proxy_for") else "COMEX"
        lines.append(
            f"白银期权({tag}) Put/Call OI 比为 {fmt(silver_opt.get('put_call_oi_ratio'), 2)}，"
            f"ATM跨式隐含波动区间约 {fmt(silver_opt.get('implied_move_pct'), 2, '%')}。"
        )

    stance: list[str] = []
    if signal.get("tips_1m_change_bp") is not None:
        if signal["tips_1m_change_bp"] > 20:
            stance.append("实际利率快速上行，通常压制无息资产估值")
        elif signal["tips_1m_change_bp"] < -20:
            stance.append("实际利率回落，对黄金估值更友好")
    if vix.get("latest", {}).get("value") and vix["latest"]["value"] > 25:
        stance.append("VIX处于偏高区间，避险需求可能放大日内波动")
    if dxy.get("pct_1m") is not None:
        if dxy["pct_1m"] > 2:
            stance.append("美元走强对商品价格形成汇率压力")
        elif dxy["pct_1m"] < -2:
            stance.append("美元走弱为以美元计价商品提供顺风")
    if not stance:
        stance.append("当前信号偏中性，重点观察实际利率、美元与金银比价是否同向突破")
    lines.append("综合判断：" + "；".join(stance) + "。")
    return "\n\n".join(lines)


def build_ai_prompt(data: dict[str, Any]) -> str:
    compact = {
        "generated_at": data["generated_at"],
        "fred_latest": {k: v.get("latest") for k, v in data["fred"].items()},
        "fred_changes": {k: {"change_1w": v.get("change_1w"), "change_1m": v.get("change_1m")} for k, v in data["fred"].items()},
        "markets": {
            k: {
                "latest": v.get("latest"),
                "pct_1d": v.get("pct_1d"),
                "pct_1w": v.get("pct_1w"),
                "pct_1m": v.get("pct_1m"),
            }
            for k, v in data["markets"]["daily"].items()
        },
        "options": data["options"],
        "signals": data["signals"],
    }
    return (
        "你是大宗商品宏观研究员。请用中文写一段结构化总结，面向黄金/白银研究。\n"
        "要求：1) 先给结论；2) 分析实际利率、美元、VIX、原油铜、金银期权Put/Call；"
        "3) 标明COMEX期权如果是ETF代理就降低置信度；4) 不要编造数据；5) 给出下周观察点。\n\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
    )


def call_openai_summary(data: dict[str, Any]) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": "你输出专业、克制、可核查的中文市场研究摘要。"},
            {"role": "user", "content": build_ai_prompt(data)},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        texts: list[str] = []
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    texts.append(content.get("text", ""))
        return "\n".join(x for x in texts if x).strip() or None
    except Exception as exc:
        log(f"  OpenAI summary failed, using local summary: {exc}")
        return None


def call_mimo_summary(data: dict[str, Any]) -> str | None:
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.environ.get("MIMO_API_BASE", "https://api.xiaomimimo.com/v1").strip().rstrip("/")
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro").strip() or "mimo-v2.5-pro"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你输出专业、克制、可核查的中文市场研究摘要。"},
            {"role": "user", "content": build_ai_prompt(data)},
        ],
        "temperature": 0.25,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(str(item["text"]))
                joined = "\n".join(parts).strip()
                if joined:
                    return joined
        return None
    except Exception as exc:
        log(f"  Mimo summary failed, using fallback: {exc}")
        return None


def collect_all() -> dict[str, Any]:
    log("Collect macro rates data...")
    fred = collect_macro_series()
    log("Collect futures, VIX and cross-asset data...")
    markets = collect_market_prices()
    log("Collect gold/silver option chains...")
    options = collect_options()
    signals = rolling_real_rate_signal(fred.get("DFII10", {}), markets["daily"].get("GC=F", {}))
    data = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "data_sources": [
            "U.S. Treasury: daily real yield curve and daily nominal yield curve",
            "Yahoo Finance chart API: futures, VIX, dollar index and ETFs",
            "Yahoo Finance options API: COMEX futures options when available; GLD/SLV proxy fallback",
        ],
        "fred": fred,
        "markets": markets,
        "options": options,
        "signals": signals,
    }
    log("Generate commentary...")
    ai_summary = call_mimo_summary(data)
    summary_engine = "Mimo API" if ai_summary else ""
    if not ai_summary:
        ai_summary = call_openai_summary(data)
        summary_engine = "OpenAI API" if ai_summary else ""
    data["summary"] = ai_summary or build_local_summary(data)
    data["summary_engine"] = summary_engine or "Local rules"
    return data


def svg_line_chart(
    series: list[dict[str, Any]],
    *,
    width: int = 760,
    height: int = 280,
    color: str = "#3b82f6",
    chart_id: str = "",
) -> tuple[str, list[dict[str, str]]]:
    clean = [(p.get("date"), safe_float(p.get("value"))) for p in series if safe_float(p.get("value")) is not None]
    clean = clean[-260:]
    if len(clean) < 2:
        return "<div class='empty-chart'>图表数据不足</div>", []
    values = [v for _, v in clean if v is not None]
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        min_v -= 1
        max_v += 1
    pad = 34
    points = []
    xy_pairs: list[tuple[float, float]] = []
    for idx, (_, value) in enumerate(clean):
        if value is None:
            continue
        x = pad + idx * (width - pad * 2) / (len(clean) - 1)
        y = height - pad - (value - min_v) * (height - pad * 2) / (max_v - min_v)
        points.append(f"{x:.1f},{y:.1f}")
        xy_pairs.append((x, y))
    latest_date, latest_value = clean[-1]
    first_date, _ = clean[0]
    grid = "".join(
        f"<line x1='{pad}' x2='{width-pad}' y1='{pad + i*(height-pad*2)/4:.1f}' y2='{pad + i*(height-pad*2)/4:.1f}' />"
        for i in range(5)
    )
    bottom_y = height - pad
    area_points = " ".join(points) + f" {xy_pairs[-1][0]:.1f},{bottom_y} {xy_pairs[0][0]:.1f},{bottom_y}"
    grad_id = f"grad-{chart_id}" if chart_id else "grad-default"
    svg_html = f"""
    <svg viewBox="0 0 {width} {height}" class="line-chart" role="img"
         data-pad="{pad}" data-npoints="{len(clean)}"
         data-minv="{min_v}" data-maxv="{max_v}">
      <defs>
        <linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="{color}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <g class="grid">{grid}</g>
      <polygon fill="url(#{grad_id})" points="{area_points}" />
      <polyline fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" points="{' '.join(points)}" />
      <g class="crosshair" style="display:none">
        <line x1="0" y1="{pad}" x2="0" y2="{bottom_y}" stroke="{color}" stroke-width="1" stroke-dasharray="4,3" opacity="0.7"/>
        <circle cx="0" cy="0" r="4" fill="{color}" stroke="#fff" stroke-width="1.5"/>
      </g>
      <rect x="{pad}" y="{pad}" width="{width - pad * 2}" height="{bottom_y - pad}" fill="transparent" style="cursor:crosshair" class="chart-overlay"/>
      <text x="{pad}" y="{height - 8}" class="axis">{html.escape(str(first_date))}</text>
      <text x="{width - pad}" y="{height - 8}" text-anchor="end" class="axis">{html.escape(str(latest_date))}</text>
      <text x="{pad}" y="20" class="axis">{fmt(max_v, 2)}</text>
      <text x="{width - pad}" y="20" text-anchor="end" class="axis">最新 {fmt(latest_value, 2)}</text>
    </svg>
    """
    data_points = [{"date": str(d), "value": f"{v:.2f}"} for d, v in clean]
    return svg_html, data_points


def metric_card(title: str, value: str, sub: str = "", accent: str = "") -> str:
    style = f" style='border-left:3px solid {accent}'" if accent else ""
    return f"<article class='metric'{style}><span>{html.escape(title)}</span><strong>{html.escape(value)}</strong><small>{html.escape(sub)}</small></article>"


def option_table(option_data: dict[str, Any]) -> str:
    if option_data.get("status") != "ok":
        return f"<p class='warn'>期权链获取失败：{html.escape(option_data.get('error', 'unknown'))}</p>"
    proxy = option_data.get("proxy_for")
    note = f"<p class='note'>使用 {html.escape(option_data['label'])} 作为 {html.escape(proxy)} 的代理。</p>" if proxy else ""

    def rows(items: list[dict[str, Any]]) -> str:
        body = ""
        for item in items:
            body += (
                "<tr>"
                f"<td>{html.escape(str(item.get('contract') or ''))}</td>"
                f"<td>{fmt(item.get('strike'), 2)}</td>"
                f"<td>{fmt(item.get('mid'), 2)}</td>"
                f"<td>{fmt(item.get('open_interest'), 0)}</td>"
                f"<td>{fmt(item.get('implied_vol'), 2)}</td>"
                "</tr>"
            )
        return body or "<tr><td colspan='5'>无可用数据</td></tr>"

    return f"""
    {note}
    <div class="option-grid">
      {metric_card("标的价格", fmt(option_data.get("underlying"), 2), option_data.get("expiration") or "N/A")}
      {metric_card("Put/Call OI", fmt(option_data.get("put_call_oi_ratio"), 2), "按未平仓量")}
      {metric_card("ATM跨式", fmt(option_data.get("atm_straddle"), 2), f"隐含波动 {fmt(option_data.get('implied_move_pct'), 2, '%')}")}
    </div>
    <h4>Call 重点价位</h4>
    <table><thead><tr><th>合约</th><th>Strike</th><th>Mid</th><th>OI</th><th>IV</th></tr></thead><tbody>{rows(option_data.get("top_calls_by_oi", []))}</tbody></table>
    <h4>Put 重点价位</h4>
    <table><thead><tr><th>合约</th><th>Strike</th><th>Mid</th><th>OI</th><th>IV</th></tr></thead><tbody>{rows(option_data.get("top_puts_by_oi", []))}</tbody></table>
    """


def render_inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def markdown_to_html(markdown: str) -> str:
    blocks: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None
    list_start: int | None = None

    def flush_list() -> None:
        nonlocal list_items, list_kind, list_start
        if list_items:
            tag = "ol" if list_kind == "ol" else "ul"
            start_attr = f' start="{list_start}"' if tag == "ol" and list_start and list_start > 1 else ""
            blocks.append(f"<{tag}{start_attr}>{''.join(list_items)}</{tag}>")
        list_items = []
        list_kind = None
        list_start = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_list()
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            flush_list()
            level = min(len(heading.group(1)) + 1, 4)
            blocks.append(f"<h{level}>{render_inline_markdown(heading.group(2))}</h{level}>")
            continue

        ordered = re.match(r"^(\d+)\.\s+(.+)$", line)
        unordered = re.match(r"^[*-]\s+(.+)$", line)
        if ordered or unordered:
            kind = "ol" if ordered else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            if ordered and list_start is None:
                list_start = int(ordered.group(1))
            item_text = ordered.group(2) if ordered else unordered.group(1)
            list_items.append(f"<li>{render_inline_markdown(item_text)}</li>")
            continue

        flush_list()
        blocks.append(f"<p>{render_inline_markdown(line)}</p>")

    flush_list()
    return "\n".join(blocks)


def generate_markdown(data: dict[str, Any]) -> str:
    markets = data["markets"]["daily"]
    fred = data["fred"]
    lines = [
        f"# TIPS 黄金宏观报告 - {data['generated_at']}",
        "",
        "## 总结",
        "",
        data["summary"],
        "",
        "## 核心指标",
        "",
        "| 指标 | 最新 | 1周 | 1个月 |",
        "|---|---:|---:|---:|",
    ]
    for sid in ["DFII10", "DGS10", "DGS2"]:
        item = fred.get(sid, {})
        latest = item.get("latest") or {}
        lines.append(
            f"| {item.get('name', sid)} | {fmt(latest.get('value'), 2)} | "
            f"{fmt(item.get('change_1w'), 2)} | {fmt(item.get('change_1m'), 2)} |"
        )
    for symbol in ["GC=F", "SI=F", "CL=F", "HG=F", "^VIX", "DX-Y.NYB"]:
        item = markets.get(symbol, {})
        latest = item.get("latest") or {}
        lines.append(
            f"| {item.get('name', symbol)} | {fmt(latest.get('value'), 2)} | "
            f"{fmt(item.get('pct_1w'), 2, '%')} | {fmt(item.get('pct_1m'), 2, '%')} |"
        )
    lines.extend(["", "## 数据源", ""])
    lines.extend([f"- {src}" for src in data["data_sources"]])
    return "\n".join(lines)


def generate_html(data: dict[str, Any]) -> str:
    fred = data["fred"]
    markets = data["markets"]["daily"]
    weekly = data["markets"]["weekly"]
    options = data["options"]
    tips_latest = (fred.get("DFII10", {}).get("latest") or {}).get("value")
    gold = markets.get("GC=F", {})
    silver = markets.get("SI=F", {})
    vix = markets.get("^VIX", {})
    dxy = markets.get("DX-Y.NYB", {})
    copper = markets.get("HG=F", {})
    oil = markets.get("CL=F", {})

    cards = "".join([
        metric_card("10Y TIPS", fmt(tips_latest, 2, "%"), f"1月 {fmt(data['signals'].get('tips_1m_change_bp'), 1, 'bp')}", "#3b82f6"),
        metric_card("COMEX 黄金", fmt((gold.get("latest") or {}).get("value"), 2), f"1月 {fmt(gold.get('pct_1m'), 2, '%')}", "#d4a017"),
        metric_card("COMEX 白银", fmt((silver.get("latest") or {}).get("value"), 2), f"1月 {fmt(silver.get('pct_1m'), 2, '%')}", "#94a3b8"),
        metric_card("VIX", fmt((vix.get("latest") or {}).get("value"), 2), f"1周 {fmt(vix.get('pct_1w'), 2, '%')}", "#ef4444"),
        metric_card("美元指数", fmt((dxy.get("latest") or {}).get("value"), 2), f"1月 {fmt(dxy.get('pct_1m'), 2, '%')}", "#6366f1"),
        metric_card("铜/原油", f"{fmt((copper.get('latest') or {}).get('value'), 2)} / {fmt((oil.get('latest') or {}).get('value'), 2)}", "增长与通胀线索", "#f59e0b"),
    ])

    tips_svg, tips_pts = svg_line_chart((fred.get("DFII10", {}).get("points") or []), color="#3b82f6", chart_id="tips")
    gold_svg, gold_pts = svg_line_chart((weekly.get("GC=F", {}).get("points") or []), color="#d4a017", chart_id="gold-wk")
    silver_svg, silver_pts = svg_line_chart((weekly.get("SI=F", {}).get("points") or []), color="#94a3b8", chart_id="silver-wk")
    vix_svg, vix_pts = svg_line_chart((vix.get("points") or []), color="#ef4444", chart_id="vix")

    gold_opt_html = option_table(options.get("GC=F", {}))
    silver_opt_html = option_table(options.get("SI=F", {}))

    chart_data_json = json.dumps({
        "tips": {"points": tips_pts, "label": "10Y TIPS"},
        "gold-wk": {"points": gold_pts, "label": "COMEX 黄金周线"},
        "silver-wk": {"points": silver_pts, "label": "COMEX 白银周线"},
        "vix": {"points": vix_pts, "label": "VIX"},
    }, ensure_ascii=False)

    interactive_js = r"""
(function(){
  var charts=window.__charts||{};
  document.querySelectorAll('.chart-wrap[data-chart]').forEach(function(wrap){
    var key=wrap.getAttribute('data-chart');
    var info=charts[key];
    if(!info||!info.points||!info.points.length)return;
    var svg=wrap.querySelector('svg');
    if(!svg)return;
    var crosshair=svg.querySelector('.crosshair');
    var overlay=svg.querySelector('.chart-overlay');
    var tooltip=wrap.querySelector('.chart-tooltip');
    if(!overlay||!crosshair||!tooltip)return;
    var pts=info.points;
    var pad=parseFloat(svg.getAttribute('data-pad'))||34;
    var np=parseFloat(svg.getAttribute('data-npoints'))||pts.length;
    var minV=parseFloat(svg.getAttribute('data-minv'));
    var maxV=parseFloat(svg.getAttribute('data-maxv'));
    var vb=svg.viewBox.baseVal;
    var vw=vb.width,vh=vb.height;
    var chLine=crosshair.querySelector('line');
    var chDot=crosshair.querySelector('circle');
    overlay.addEventListener('mousemove',function(e){
      var rect=svg.getBoundingClientRect();
      var mouseXSvg=(e.clientX-rect.left)/rect.width*vw;
      var idx=Math.round((mouseXSvg-pad)/(vw-pad*2)*(np-1));
      if(idx<0)idx=0;if(idx>=np)idx=np-1;
      var pt=pts[idx];if(!pt)return;
      var val=parseFloat(pt.value);
      var sx=pad+idx*(vw-pad*2)/(np-1);
      var sy=vh-pad-(val-minV)*(vh-pad*2)/(maxV-minV);
      chLine.setAttribute('x1',sx);chLine.setAttribute('x2',sx);
      chDot.setAttribute('cx',sx);chDot.setAttribute('cy',sy);
      crosshair.style.display='';
      var cssX=(sx/vw)*rect.width;
      var cssY=(sy/vh)*rect.height;
      tooltip.innerHTML='<div class="tt-date">'+pt.date+'</div><div class="tt-val">'+info.label+'：<strong>'+pt.value+'</strong></div>';
      tooltip.style.display='block';
      var tw=tooltip.offsetWidth;
      var left=cssX-tw/2;
      if(left<0)left=4;if(left+tw>rect.width)left=rect.width-tw-4;
      tooltip.style.left=left+'px';
      tooltip.style.top=(cssY-10)+'px';
      tooltip.style.transform='translateY(-100%)';
    });
    overlay.addEventListener('mouseleave',function(){
      crosshair.style.display='none';
      tooltip.style.display='none';
    });
  });
})();"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TIPS 黄金宏观监控</title>
  <style>
    :root {{
      --navy: #0f172a;
      --gold: #d4a017;
      --blue: #3b82f6;
      --silver: #94a3b8;
      --crimson: #ef4444;
      --emerald: #10b981;
      --slate: #334155;
      --soft-bg: #f1f5f9;
      --amber: #f59e0b;
      --indigo: #6366f1;
      --panel: #ffffff;
      --line: #e2e8f0;
      --muted: #64748b;
    }}
    * {{ box-sizing: border-box; margin: 0; }}
    body {{ font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; color: var(--slate); background: var(--soft-bg); }}
    header {{
      background: linear-gradient(160deg, #0a0f1a 0%, #0f172a 40%, #1a2744 100%);
      color: white; padding: 0;
      position: relative; overflow: hidden;
    }}
    header::before {{
      content: ""; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(ellipse at 80% 20%, rgba(212,160,23,0.08) 0%, transparent 60%);
      pointer-events: none;
    }}
    header::after {{
      content: ""; position: absolute; bottom: 0; left: 0; right: 0; height: 3px;
      background: linear-gradient(90deg, var(--gold) 0%, #e8b84a 50%, var(--gold) 100%);
    }}
    .header-inner {{
      max-width: 1240px; margin: 0 auto;
      padding: 36px clamp(18px, 4vw, 48px) 32px;
      position: relative; z-index: 1;
    }}
    .header-top {{ display: flex; align-items: flex-end; gap: 16px; margin-bottom: 10px; }}
    .header-top h1 {{
      margin: 0; font-size: clamp(28px, 4vw, 44px);
      letter-spacing: -0.03em; font-weight: 800;
      background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .header-badge {{
      display: inline-flex; align-items: center; gap: 5px;
      background: rgba(212,160,23,0.15); border: 1px solid rgba(212,160,23,0.3);
      border-radius: 20px; padding: 4px 12px; font-size: 12px; color: #e8b84a;
      white-space: nowrap; margin-bottom: 6px;
    }}
    .header-badge::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--gold); }}
    header .meta {{ color: #64748b; font-size: 13px; line-height: 1.6; }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 32px clamp(14px, 3vw, 32px) 56px; }}
    section {{ margin: 32px 0; }}
    h2 {{
      font-size: 20px; margin: 0 0 16px; font-weight: 700; letter-spacing: -0.01em; color: var(--slate);
      padding-left: 14px; border-left: 4px solid var(--gold); line-height: 1.3;
    }}
    .panel > h2 {{ border-left-color: var(--gold); }}
    .panel-chart > h2 {{ border-left-color: var(--blue); }}
    h3 {{ margin: 0 0 12px; font-size: 18px; }}
    h4 {{ margin: 18px 0 8px; font-size: 14px; font-weight: 600; color: var(--slate); text-transform: uppercase; letter-spacing: 0.03em; }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }}
    .metric {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
      padding: 14px 14px 14px 16px; min-height: 104px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .metric:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
    .metric span {{ display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; }}
    .metric strong {{ display: block; font-size: 24px; margin: 10px 0 6px; overflow-wrap: anywhere; }}
    .metric small {{ color: var(--muted); font-size: 13px; }}
    .panel {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
      padding: 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .summary {{ line-height: 1.78; font-size: 15px; }}
    .summary h3 {{ margin: 4px 0 12px; font-size: 19px; }}
    .summary h4 {{ margin: 18px 0 8px; font-size: 16px; text-transform: none; letter-spacing: normal; }}
    .summary p {{ margin: 0 0 12px; }}
    .summary ul, .summary ol {{ margin: 0 0 14px 22px; padding: 0; }}
    .summary li {{ margin: 6px 0; }}
    .summary strong {{ font-weight: 700; }}
    .grid-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .line-chart {{ width: 100%; height: auto; display: block; }}
    .grid line {{ stroke: var(--line); stroke-width: 1; stroke-dasharray: 4,3; }}
    .axis {{ fill: var(--muted); font-size: 11px; }}
    .empty-chart {{ min-height: 220px; display: grid; place-items: center; color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    thead th {{
      background: var(--navy); color: #e2e8f0; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.04em; font-size: 12px;
      padding: 10px 10px; border-bottom: none;
    }}
    thead th:first-child {{ border-radius: 6px 0 0 0; }}
    thead th:last-child {{ border-radius: 0 6px 0 0; }}
    td {{ border-bottom: 1px solid var(--line); text-align: left; padding: 9px 10px; vertical-align: top; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    tbody tr:hover {{ background: #eff6ff; }}
    .option-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 8px; }}
    .note {{ color: var(--gold); font-weight: 600; font-size: 13px; }}
    .warn {{ color: var(--crimson); }}
    footer {{
      color: var(--muted); font-size: 13px; margin-top: 36px; line-height: 1.7;
      border-top: 1px solid var(--line); padding-top: 20px; text-align: center;
    }}
    .chart-wrap {{ position: relative; }}
    .chart-tooltip {{
      display: none; position: absolute; z-index: 10;
      background: rgba(15,23,42,0.92); color: #e2e8f0;
      border-radius: 8px; padding: 8px 12px; font-size: 13px;
      pointer-events: none; white-space: nowrap;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }}
    .chart-tooltip::after {{
      content: ""; position: absolute; bottom: -6px; left: 50%; transform: translateX(-50%);
      border-left: 6px solid transparent; border-right: 6px solid transparent;
      border-top: 6px solid rgba(15,23,42,0.92);
    }}
    .chart-tooltip .tt-date {{ font-size: 11px; color: #94a3b8; margin-bottom: 2px; }}
    .chart-tooltip .tt-val strong {{ color: #f1f5f9; }}
    @media (max-width: 980px) {{
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid-2 {{ grid-template-columns: 1fr; }}
      .option-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .metrics {{ grid-template-columns: 1fr; }}
      .metric strong {{ font-size: 21px; }}
      table {{ font-size: 12px; }}
      .header-top {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
    }}
  </style>
  <script>window.__charts={chart_data_json};</script>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="header-top">
        <h1>TIPS 黄金宏观监控</h1>
        <span class="header-badge">Macro Dashboard</span>
      </div>
      <div class="meta">生成时间：{html.escape(data['generated_at'])} · 总结引擎：{html.escape(data.get('summary_engine', 'Unknown'))}</div>
    </div>
  </header>
  <main>
    <section class="metrics">{cards}</section>
    <section class="panel">
      <h2>研究总结</h2>
      <div class="summary">{markdown_to_html(data["summary"])}</div>
    </section>
    <section class="grid-2">
      <article class="panel panel-chart">
        <h2>10Y TIPS 实际收益率</h2>
        <div class="chart-wrap" data-chart="tips">{tips_svg}<div class="chart-tooltip"></div></div>
      </article>
      <article class="panel panel-chart">
        <h2>COMEX 黄金周线</h2>
        <div class="chart-wrap" data-chart="gold-wk">{gold_svg}<div class="chart-tooltip"></div></div>
      </article>
      <article class="panel panel-chart">
        <h2>COMEX 白银周线</h2>
        <div class="chart-wrap" data-chart="silver-wk">{silver_svg}<div class="chart-tooltip"></div></div>
      </article>
      <article class="panel panel-chart">
        <h2>VIX 指数</h2>
        <div class="chart-wrap" data-chart="vix">{vix_svg}<div class="chart-tooltip"></div></div>
      </article>
    </section>
    <section class="grid-2">
      <article class="panel">
        <h2>黄金 PUT/CALL 价位</h2>
        {gold_opt_html}
      </article>
      <article class="panel">
        <h2>白银 PUT/CALL 价位</h2>
        {silver_opt_html}
      </article>
    </section>
    <section class="panel">
      <h2>数据源与口径</h2>
      <table>
        <thead><tr><th>数据来源</th></tr></thead>
        <tbody>
          {''.join(f"<tr><td>{html.escape(src)}</td></tr>" for src in data["data_sources"])}
        </tbody>
      </table>
    </section>
    <footer>
      本报告用于研究辅助，不构成投资建议。COMEX 期权公开接口不可用时会自动回退到 GLD/SLV 期权代理，方向可参考，但价位与合约乘数并不等同于 COMEX。
    </footer>
  </main>
  <script>{interactive_js}</script>
</body>
</html>"""


def save_outputs(data: dict[str, Any]) -> tuple[Path, Path, Path]:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y-%m-%d")
    json_path = DATA_DIR / f"{stamp}.json"
    md_path = MD_DIR / f"{stamp}.md"
    html_path = REPORTS_DIR / "latest.html"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(generate_markdown(data), encoding="utf-8")
    html_path.write_text(generate_html(data), encoding="utf-8")
    return json_path, md_path, html_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate TIPS/gold commodity macro report.")
    parser.add_argument("--open", action="store_true", help="Open latest.html after generation.")
    args = parser.parse_args()

    ensure_dirs()
    data = collect_all()
    json_path, md_path, html_path = save_outputs(data)
    log("")
    log(f"JSON saved: {json_path}")
    log(f"Markdown saved: {md_path}")
    log(f"Web page saved: {html_path}")
    if args.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
