#!/usr/bin/env python3
"""Build www/morning.json for the morning dashboard (run from cron, every 5 minutes).

Every section is {"ok": bool, "at": iso, "data" | "error"} - a source that fails
shows as unavailable on the page, never as an empty or zero value. A failed refresh
keeps the last good reading, flagged stale.
Sun/moon paths use astral; NOTE astral.moon.elevation ignores tzinfo and treats
the wall clock as UTC - always pass UTC datetimes.

Usage: build.py [--force] [--config path]     (default config: $DASHBOARD_CONFIG or ./config.json)
"""
import json
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from astral import LocationInfo, moon as amoon, sun as asun

HERE = Path(__file__).resolve().parent
OUT = HERE / "www" / "morning.json"
SEEN = HERE / "state" / "seen"       # touched by serve.py whenever the page asks for morning.json
CFG = {}
TZ = timezone.utc

SECTIONS = ("weather", "sky", "tide", "prices", "quote", "reminders", "log")
DEFAULTS = {"name": "", "alarm": None, "sea": None, "log": {"title": "Latest notes", "file": None},
            "reminders_file": "reminders.json", "radio_streams": [],
            "sections": {s: s in ("weather", "sky", "quote") for s in SECTIONS}}

WMO = {0: ("Clear", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"), 3: ("Overcast", "☁️"),
       45: ("Fog", "🌫️"), 48: ("Fog", "🌫️"), 51: ("Drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Drizzle", "🌦️"),
       61: ("Rain", "🌧️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"), 80: ("Showers", "🌦️"),
       81: ("Showers", "🌦️"), 82: ("Heavy showers", "⛈️"), 95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm", "⛈️")}


def load_config(path):
    global CFG, TZ
    p = Path(path).expanduser()
    if not p.exists():
        sys.exit(f"config not found: {p}  (copy config.example.json to config.json and edit it)")
    user = json.loads(p.read_text())
    CFG = {**DEFAULTS, **user, "sections": {**DEFAULTS["sections"], **user.get("sections", {})},
           "log": {**DEFAULTS["log"], **user.get("log", {})}, "_dir": p.parent}
    for key in ("timezone", "location"):
        if key not in CFG:
            sys.exit(f"config: '{key}' is required")
    if CFG["sections"]["tide"] and not CFG["sea"]:
        sys.exit("config: the tide section needs a 'sea' position (or set sections.tide to false)")
    TZ = ZoneInfo(CFG["timezone"])


def cfg_path(value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else CFG["_dir"] / p


def get(url):
    # Open-Meteo answers 503 now and then: retry before giving up.
    for wait in (3, 8, None):
        try:
            with urlopen(Request(url, headers={"User-Agent": "morning-dashboard"}), timeout=15) as r:
                return json.load(r)
        except Exception:
            if wait is None:
                raise
            time.sleep(wait)


def now():
    return datetime.now(TZ)


def weather():
    loc = CFG["location"]
    d = get(f"https://api.open-meteo.com/v1/forecast?latitude={loc['latitude']}&longitude={loc['longitude']}"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
            "&hourly=temperature_2m"
            "&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max"
            f"&timezone={urlquote(CFG['timezone'], safe='')}&forecast_days=1")
    c, day = d["current"], d["daily"]
    text, icon = WMO.get(c["weather_code"], ("-", "🌡️"))
    return {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
            "humidity": c["relative_humidity_2m"], "wind": round(c["wind_speed_10m"]),
            "text": text, "icon": icon,
            "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]),
            "uv": day["uv_index_max"][0],
            "hourly": [round(t) for t in d["hourly"]["temperature_2m"][:24]],   # 00..23 today
            "sunrise": day["sunrise"][0][-5:], "sunset": day["sunset"][0][-5:]}


def moon_phase():
    # Days since a known new moon (2000-01-06 18:14 UTC) modulo the synodic month.
    synodic = 29.530588853
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    age = ((datetime.now(timezone.utc) - ref).total_seconds() / 86400) % synodic
    illum = round((1 - math.cos(2 * math.pi * age / synodic)) / 2 * 100)
    names = [("New moon", "🌑"), ("Waxing crescent", "🌒"), ("First quarter", "🌓"), ("Waxing gibbous", "🌔"),
             ("Full moon", "🌕"), ("Waning gibbous", "🌖"), ("Last quarter", "🌗"), ("Waning crescent", "🌘")]
    name, icon = names[int((age / synodic) * 8 + 0.5) % 8]
    return {"name": name, "icon": icon, "illumination": illum}


def sky():
    """Sun + moon elevation across today (every 10 min) and rise/set times."""
    loc = CFG["location"]
    obs = LocationInfo(loc.get("label", ""), "", CFG["timezone"], loc["latitude"], loc["longitude"]).observer
    day = now().date()
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)

    def hm(fn):
        try:
            return fn(obs, day, tzinfo=TZ).strftime("%H:%M")
        except Exception:            # no rise/set on this date (happens for the moon)
            return None
    steps = [start + timedelta(minutes=10 * i) for i in range(145)]
    utc = [t.astimezone(timezone.utc) for t in steps]
    s = asun.sun(obs, day, tzinfo=TZ)
    return {"sun": {"rise": s["sunrise"].strftime("%H:%M"), "set": s["sunset"].strftime("%H:%M"),
                    "noon": s["noon"].strftime("%H:%M"),
                    "elev": [round(asun.elevation(obs, t), 1) for t in utc]},
            "moon": {"rise": hm(amoon.moonrise), "set": hm(amoon.moonset),
                     "elev": [round(amoon.elevation(obs, t), 1) for t in utc], **moon_phase()}}


def tide():
    # 15-minute sea level from Open-Meteo's marine model; turning points +-8 min.
    sea = CFG["sea"]
    d = get(f"https://marine-api.open-meteo.com/v1/marine?latitude={sea['latitude']}&longitude={sea['longitude']}"
            "&minutely_15=sea_level_height_msl&hourly=sea_surface_temperature"
            f"&timezone={urlquote(CFG['timezone'], safe='')}&forecast_days=2")
    m = d["minutely_15"]
    t, lv = m["time"], m["sea_level_height_msl"]
    today = now().date().isoformat()
    events = []
    for i in range(1, len(lv) - 1):
        if None in (lv[i - 1], lv[i], lv[i + 1]):
            continue
        kind = "High" if lv[i] > lv[i - 1] and lv[i] >= lv[i + 1] else \
               "Low" if lv[i] < lv[i - 1] and lv[i] <= lv[i + 1] else None
        if kind and t[i][:10] == today:
            events.append({"kind": kind, "time": t[i][-5:], "m": lv[i]})
    curve = [v for tt, v in zip(t, lv) if tt[:10] == today]
    if None in curve or not curve:
        raise ValueError("no sea level data for this position (is it open water?)")
    cur = now().strftime("%Y-%m-%dT%H:00")
    h = d["hourly"]
    sst = next((s for tt, s in zip(h["time"], h["sea_surface_temperature"]) if tt == cur), None)
    return {"events": events, "curve": curve, "sea_temp": sst}


def prices():
    # Gold price = spot XAU (gold-api.com). Its 24h direction comes from PAX Gold on
    # CoinGecko (a 1:1 gold-backed token) because the spot API has no history.
    cg = get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,pax-gold"
             "&vs_currencies=usd&include_24hr_change=true")
    gold = get("https://api.gold-api.com/price/XAU")
    return {"btc": {"usd": cg["bitcoin"]["usd"], "chg24": round(cg["bitcoin"]["usd_24h_change"], 1)},
            "gold": {"usd": round(gold["price"]), "chg24": round(cg["pax-gold"]["usd_24h_change"], 1)}}


def quote(prev):
    # ZenQuotes is rate-limited and changes daily - fetch once per day.
    if prev.get("ok") and prev.get("data", {}).get("date") == now().date().isoformat():
        return prev["data"]
    q = get("https://zenquotes.io/api/today")[0]
    return {"text": q["q"], "author": q["a"], "date": now().date().isoformat()}


def log_feed():
    """Last 3 entries of a markdown log: lines like '- 2026-10-19 — [tag] First sentence. More.'"""
    if not CFG["log"]["file"]:
        raise FileNotFoundError("log.file is not set in the config")
    rows = []
    for ln in cfg_path(CFG["log"]["file"]).read_text().splitlines():
        m = re.match(r"- (\d{4}-\d{2}-\d{2}) — \[([^\]]+)\]\s*(.+)", ln)
        if m:
            rows.append(m.groups())
    if not rows:
        raise FileNotFoundError("no entries found")
    lines = []
    for date, tag, text in rows[-3:][::-1]:
        text = re.sub(r"[`*_]", "", text)
        text = re.split(r"(?<!incl)(?<!e\.g)\.\s+(?=[A-Z])", text, maxsplit=1)[0].rstrip(".")  # first sentence
        lines.append({"head": f"{tag} · {datetime.fromisoformat(date).strftime('%d %b')}", "text": text})
    return {"lines": lines}


def reminders():
    """Today's items from a JSON list: [{"at": "2026-10-19T18:30:00", "msg": "...", "status": "pending|fired"}]"""
    f = cfg_path(CFG["reminders_file"])
    items = json.loads(f.read_text()) if f.exists() else []
    today = now().date().isoformat()
    todays = [r for r in items if r["at"][:10] == today and r["status"] in ("pending", "fired")]
    return [{"time": r["at"][11:16], "msg": r["msg"], "done": r["status"] == "fired"}
            for r in sorted(todays, key=lambda r: r["at"])]


def section(fn, *args, prev=None):
    try:
        return {"ok": True, "at": now().isoformat(timespec="seconds"), "data": fn(*args)}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:160]
        if prev and prev.get("ok") and "data" in prev:
            # keep the last good reading, flagged stale, rather than an error tile
            return {**prev, "stale": True, "error": err}
        return {"ok": False, "at": now().isoformat(timespec="seconds"),
                "error": err, "trace": traceback.format_exc(limit=1)[-300:]}


def alarm_time(n):
    """Today's alarm as a datetime, or None when no alarm is configured."""
    a = CFG["alarm"]
    if not a:
        return None
    hhmm = a["weekend"] if n.weekday() in a.get("weekend_days", [5, 6]) else a["weekday"]
    h, m = map(int, hhmm.split(":"))
    return n.replace(hour=h, minute=m, second=0, microsecond=0)


def due():
    """Build only around the alarm or while the page is on screen (no network calls otherwise).
    Window T-30 .. T+75 min around the alarm; the last 12 minutes before T rebuild on every
    tick (age >= 4 min) so the data is fresh when the alarm fires. Without an alarm in the
    config, builds happen only while the page is on screen."""
    n = now()
    t = alarm_time(n)
    age = (n.timestamp() - OUT.stat().st_mtime) / 60 if OUT.exists() else 1e9
    if t:
        mins = (n - t).total_seconds() / 60
        if -12 <= mins < 0:
            return age >= 4, "pre-alarm"
        if -30 <= mins <= 75:
            return age >= 14, "alarm window"
    try:
        seen = (n.timestamp() - SEEN.stat().st_mtime) / 60      # page polls morning.json every 60 s while shown
    except OSError:
        seen = 1e9
    return (seen <= 10 and age >= 14), "page on screen" if seen <= 10 else "idle"


def page_config():
    """The part of the config the page needs (no file paths)."""
    return {"name": CFG["name"], "timezone": CFG["timezone"], "location": CFG["location"].get("label", ""),
            "sections": CFG["sections"], "log_title": CFG["log"]["title"], "radio_streams": CFG["radio_streams"]}


def main():
    args = sys.argv[1:]
    cfg = args[args.index("--config") + 1] if "--config" in args else os.environ.get("DASHBOARD_CONFIG", HERE / "config.json")
    load_config(cfg)
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    if "--force" not in args:
        go, why = due()
        if not go:
            return                      # idle: no API calls, no log line
    on = CFG["sections"]
    jobs = {"weather": lambda: section(weather, prev=prev.get("weather")),
            "sky": lambda: section(sky),
            "tide": lambda: section(tide, prev=prev.get("tide")),
            "prices": lambda: section(prices, prev=prev.get("prices")),
            "quote": lambda: section(quote, prev.get("quote", {}), prev=prev.get("quote")),
            "reminders": lambda: section(reminders),
            "log": lambda: section(log_feed)}
    out = {"built": now().isoformat(timespec="seconds"), "config": page_config()}
    out.update({k: job() for k, job in jobs.items() if on.get(k)})
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    tmp.replace(OUT)
    secs = {k: v for k, v in out.items() if isinstance(v, dict) and "ok" in v}
    bad = [k for k, v in secs.items() if not v["ok"]]
    old = [k for k, v in secs.items() if v.get("stale")]
    print(f"{out['built']} built ({why if '--force' not in args else 'forced'}); unavailable: {bad or 'none'}; stale: {old or 'none'}")


if __name__ == "__main__":
    main()
