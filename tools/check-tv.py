#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ไล่ยิงทุกช่องใน tv.json แล้วเอาช่องที่ตายจริงออก

**ไม่ตัดทันทีที่ fail ครั้งเดียว** — ช่องสตรีมล่มชั่วคราวเป็นเรื่องปกติมาก
(เคสจริง: Dopa Channel ผ่านตอนเช้า พอบ่ายต่อไม่ติด แล้ววันต่อมากลับมาปกติ)
ถ้าตัดทันทีจะเสียช่องดีๆ ไปฟรีๆ เลยนับ fail ติดกันครบ 3 รอบก่อนค่อยตัด

ไฟล์ที่เกี่ยวข้อง:
  tv.json          รายการช่องที่รถใช้จริง  (สคริปต์นี้แก้)
  tv-health.json   ตัวนับ fail ของแต่ละช่อง (สคริปต์นี้สร้าง/แก้)
  tv-removed.json  ช่องที่ถูกตัดออก เก็บไว้กู้คืนทีหลังได้ (ไม่หายไปเฉยๆ)

รัน:  python tools/check-tv.py tv.json
exit 0 = tv.json ไม่เปลี่ยน / 10 = เปลี่ยน (workflow เอาไปเช็คว่าต้อง commit มั้ย)
"""

import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Linux; Android 9; SM-G960F) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
TIMEOUT = 15
WORKERS = 8

# ต้อง fail ติดกันกี่รอบถึงตัดออก (รันวันละครั้ง = ตายจริง 3 วันติด)
FAIL_LIMIT = 3

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, IOError):
        return default


def save(path, data):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write(u"\n")


def probe(ch):
    """เล่นได้จริงมั้ย — ต้องตอบ 200 และเป็น playlist ที่มีเนื้อข้างใน"""
    url = ch.get("url", "")
    if not url:
        return ch, False, "ไม่มี url"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        r = urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX)
        body = r.read(6000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return ch, False, "HTTP %d" % e.code
    except Exception as e:
        return ch, False, type(e).__name__

    if r.status != 200:
        return ch, False, "status %s" % r.status
    if "#EXTM3U" not in body:
        return ch, False, "ไม่ใช่ playlist"
    # master ต้องมี variant, media ต้องมี segment — ไฟล์ว่างเปล่าถือว่าตาย
    if "#EXT-X-STREAM-INF" not in body and "#EXTINF" not in body:
        return ch, False, "playlist ว่าง"
    return ch, True, "OK"


def main(argv):
    tv_path = argv[1] if len(argv) > 1 else "tv.json"
    base = os.path.dirname(os.path.abspath(tv_path))
    health_path = os.path.join(base, "tv-health.json")
    removed_path = os.path.join(base, "tv-removed.json")

    channels = load(tv_path, None)
    if not channels:
        sys.stderr.write("อ่าน %s ไม่ได้ หรือไม่มีช่องเลย\n" % tv_path)
        return 1

    health = load(health_path, {})
    removed = load(removed_path, [])

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(probe, channels))

    keep = []
    dropped = []
    today = time.strftime("%Y-%m-%d")

    for ch, ok, why in results:
        url = ch.get("url", "")
        rec = health.get(url, {"fails": 0})
        if ok:
            if rec.get("fails"):
                print("ฟื้น     %s (เคย fail %d รอบ)" % (ch.get("name"), rec["fails"]))
            health[url] = {"fails": 0, "last_ok": today}
            keep.append(ch)
            continue

        rec["fails"] = rec.get("fails", 0) + 1
        rec["last_error"] = why
        rec["last_check"] = today
        health[url] = rec

        if rec["fails"] >= FAIL_LIMIT:
            print("ตัดออก   %-24s %s (fail %d รอบติด)" % (ch.get("name"), why, rec["fails"]))
            entry = dict(ch)
            entry["removed_on"] = today
            entry["reason"] = why
            dropped.append(entry)
        else:
            print("เฝ้าดู   %-24s %s (fail %d/%d)"
                  % (ch.get("name"), why, rec["fails"], FAIL_LIMIT))
            keep.append(ch)

    save(health_path, health)

    if not dropped:
        print("\nครบ %d ช่อง ไม่มีช่องไหนต้องตัด" % len(keep))
        return 0

    # ช่องที่ตัดออกเก็บไว้ใน tv-removed.json เผื่อกลับมาใช้ได้อีก
    seen = {r.get("url") for r in removed}
    removed.extend(d for d in dropped if d.get("url") not in seen)
    save(removed_path, removed)
    save(tv_path, keep)

    print("\nตัดออก %d ช่อง เหลือ %d ช่อง" % (len(dropped), len(keep)))
    print("ช่องที่ตัดเก็บไว้ที่ tv-removed.json แล้ว")
    return 10


if __name__ == "__main__":
    sys.exit(main(sys.argv))
