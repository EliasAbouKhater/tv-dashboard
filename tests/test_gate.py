#!/usr/bin/env python3
"""due() gate, stale fallback and config checks of build.py (no network). Run: python3 tests/test_gate.py"""
import importlib.util, json, os, sys, tempfile
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location("build", Path(__file__).resolve().parent.parent / "build.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
D = Path(tempfile.mkdtemp()); b.OUT = D / "morning.json"; b.SEEN = D / "seen"
cfg = D / "config.json"
cfg.write_text(json.dumps({"timezone": "Europe/Lisbon", "location": {"latitude": 38.7, "longitude": -9.1},
                           "alarm": {"weekday": "07:30", "weekend": "09:00"}}))
b.load_config(cfg)
TZ = b.TZ; fails = 0

def check(name, ok):
    global fails
    fails += not ok
    print("ok  " if ok else "FAIL", name)

def case(name, when, age_min, seen_min, want):
    n = datetime(*when, tzinfo=TZ); b.now = lambda: n
    b.OUT.write_text("{}"); os.utime(b.OUT, (n.timestamp() - age_min * 60,) * 2)
    if seen_min is None: b.SEEN.unlink(missing_ok=True)
    else: b.SEEN.write_text("x"); os.utime(b.SEEN, (n.timestamp() - seen_min * 60,) * 2)
    got = b.due()[0]; check(f"{name} -> {got}", got == want)

MON, SUN = (2026, 10, 19), (2026, 10, 18)    # Sunday is a weekend day by default (Sat/Sun)
case("idle afternoon, nobody watching", (*MON, 14, 0), 500, None, False)
case("idle afternoon, page seen 3 min ago, build 20 min old", (*MON, 14, 0), 20, 3, True)
case("idle afternoon, page seen 3 min ago, build 5 min old", (*MON, 14, 0), 5, 3, False)
case("idle afternoon, page last seen 3 h ago", (*MON, 14, 0), 500, 180, False)
case("weekday 07:20 pre-alarm, build 5 min old", (*MON, 7, 20), 5, None, True)
case("weekday 07:25 pre-alarm, build 2 min old", (*MON, 7, 25), 2, None, False)
case("weekday 07:05 window, build 20 min old", (*MON, 7, 5), 20, None, True)
case("weekday 06:50 before window", (*MON, 6, 50), 500, None, False)
case("weekday 08:30 inside +75", (*MON, 8, 30), 20, None, True)
case("weekday 08:50 after window, nobody watching", (*MON, 8, 50), 500, None, False)
case("weekend 08:55 pre-alarm", (*SUN, 8, 55), 10, None, True)
case("weekend 07:30 is not the weekend alarm time", (*SUN, 7, 30), 500, None, False)

# no alarm configured: only builds while the page is on screen
b.CFG["alarm"] = None
case("no alarm, 07:20, nobody watching", (*MON, 7, 20), 500, None, False)
case("no alarm, page seen 1 min ago", (*MON, 7, 20), 30, 1, True)
b.CFG["alarm"] = {"weekday": "07:30", "weekend": "09:00"}

# stale fallback: a failing refresh keeps the last good data, flagged
good = {"ok": True, "at": "x", "data": {"temp": 31}}
def boom(): raise RuntimeError("HTTP Error 503")
s = b.section(boom, prev=good)
check("failed refresh keeps last good data, stale=true", s["ok"] and s["stale"] and s["data"] == {"temp": 31} and "503" in s["error"])
s = b.section(boom, prev=None)
check("failed refresh with no prior data is an error section", (not s["ok"]) and "503" in s["error"])

# log feed + reminders parsing
log = D / "log.md"; log.write_text("- 2026-10-19 — [proj] Use X. Because Y.\n- 2026-10-20 — [other] Ship it e.g. today.\n")
b.CFG["log"] = {"title": "t", "file": str(log)}
lines = b.log_feed()["lines"]
check("log feed: newest first, first sentence only", lines[0]["text"] == "Ship it e.g. today" and lines[1]["text"] == "Use X")
n = datetime(*MON, 12, 0, tzinfo=TZ); b.now = lambda: n
rf = D / "r.json"; rf.write_text(json.dumps([{"at": "2026-10-19T18:30:00", "msg": "bread", "status": "pending"},
                                              {"at": "2026-10-18T18:30:00", "msg": "old", "status": "pending"}]))
b.CFG["reminders_file"] = str(rf)
check("reminders: today's only", b.reminders() == [{"time": "18:30", "msg": "bread", "done": False}])
sys.exit(1 if fails else 0)
