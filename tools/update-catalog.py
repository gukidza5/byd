#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
อัปเดต apps.json ให้ชี้ไปเวอร์ชันล่าสุดอัตโนมัติ — ดึงจากแหล่งทางการเท่านั้น

รายการไหนอยากให้อัปออโต้ ใส่ "source" เข้าไปในรายการนั้น:

    { "name": "NewPipe", "packageName": "org.schabi.newpipe", ...,
      "source": { "type": "github", "repo": "TeamNewPipe/NewPipe" } }

    { "name": "Aurora Store", "packageName": "com.aurora.store", ...,
      "source": { "type": "fdroid" } }

type ที่รองรับ:
  github  - GitHub Releases ของโปรเจกต์นั้นเอง (repo = "owner/name")
            เลือก asset ที่ลงท้าย .apk ตัวแรกที่ไม่ใช่ debug/sources
  fdroid  - F-Droid official repo (ใช้ packageName ของรายการนั้น)
            URL ไฟล์ = https://f-droid.org/repo/<package>_<versionCode>.apk

รายการที่ไม่มี "source" จะไม่ถูกแตะเลย — พวก apkUrl ที่เป็นลิงก์ "ล่าสุดเสมอ"
(เช่น f-droid.org/F-Droid.apk, telegram.org/dl/android/apk) ไม่ต้องใส่ source
เพราะมันชี้ตัวล่าสุดอยู่แล้ว

รันเอง:  python tools/update-catalog.py apps.json
exit 0 = ไม่มีอะไรเปลี่ยน / 10 = มีการเปลี่ยน (workflow เอาไปเช็คว่าต้อง commit มั้ย)
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request

UA = "byd-media-center-catalog-updater"
TIMEOUT = 30


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and "api.github.com" in url:
        # ไม่ใส่ก็ได้ แต่ rate limit ของ GitHub แบบไม่ล็อกอินคือ 60 ครั้ง/ชม./IP
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_github(source):
    """คืน (apkUrl, version, versionCode) จาก release ล่าสุดของ repo"""
    repo = source.get("repo", "").strip()
    if not repo:
        raise ValueError('source.repo ว่าง')

    data = fetch_json("https://api.github.com/repos/%s/releases/latest" % repo)

    assets = data.get("assets") or []
    apks = [a for a in assets if (a.get("name") or "").lower().endswith(".apk")]
    if not apks:
        raise ValueError("release ล่าสุดไม่มีไฟล์ .apk")

    # เลี่ยง debug/sources ถ้ามีตัวเลือกอื่น
    def rank(a):
        n = (a.get("name") or "").lower()
        return (1 if ("debug" in n or "sources" in n) else 0, n)

    apks.sort(key=rank)
    best = apks[0]

    tag = (data.get("tag_name") or "").strip()
    version = tag[1:] if tag.lower().startswith("v") else tag

    # GitHub ไม่บอก versionCode — ปล่อย 0 แล้วให้แอปเทียบ versionName แทน
    return best.get("browser_download_url"), version, 0


def resolve_fdroid(source, package):
    """คืน (apkUrl, version, versionCode) จาก F-Droid official repo"""
    pkg = (source.get("package") or package).strip()
    if not pkg:
        raise ValueError("ไม่รู้ packageName")

    data = fetch_json("https://f-droid.org/api/v1/packages/%s" % pkg)

    code = data.get("suggestedVersionCode")
    name = ""
    for entry in (data.get("packages") or []):
        if entry.get("versionCode") == code:
            name = entry.get("versionName") or ""
            break
    if not code:
        raise ValueError("F-Droid ไม่บอก suggestedVersionCode")

    url = "https://f-droid.org/repo/%s_%d.apk" % (pkg, int(code))
    return url, name, int(code)


def resolve(app):
    source = app.get("source") or {}
    kind = (source.get("type") or "").strip().lower()
    if kind == "github":
        return resolve_github(source)
    if kind == "fdroid":
        return resolve_fdroid(source, app.get("packageName", ""))
    raise ValueError('source.type ไม่รองรับ: "%s"' % kind)


def main(argv):
    path = argv[1] if len(argv) > 1 else "apps.json"
    if not os.path.exists(path):
        sys.stderr.write("ไม่เจอไฟล์: %s\n" % path)
        return 1

    with io.open(path, encoding="utf-8") as f:
        apps = json.load(f)

    changed = []
    failed = []

    for app in apps:
        if not app.get("source"):
            continue
        label = app.get("name") or app.get("packageName") or "?"
        try:
            url, version, code = resolve(app)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as e:
            failed.append("%s -> %s" % (label, e))
            continue

        before = (app.get("apkUrl", ""), app.get("version", ""), app.get("versionCode", 0))
        after = (url, version, code)
        if before == after:
            print("ok       %s (%s)" % (label, version or "ไม่ระบุเวอร์ชัน"))
            continue

        app["apkUrl"] = url
        app["version"] = version
        app["versionCode"] = code
        # รายการที่อัปออโต้เป็นไฟล์เดียวเสมอ ไม่ให้ apkUrls เก่าค้างแล้วชนกัน
        app.pop("apkUrls", None)

        changed.append("%s: %s -> %s" % (label, before[1] or "?", version or "?"))
        print("UPDATED  %s -> %s" % (label, version or url))

    if failed:
        sys.stderr.write("\nดึงไม่สำเร็จ %d รายการ:\n" % len(failed))
        for f_ in failed:
            sys.stderr.write("  - %s\n" % f_)

    if not changed:
        print("\nไม่มีอะไรเปลี่ยน")
        return 0

    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(apps, f, ensure_ascii=False, indent=2)
        f.write(u"\n")

    print("\nเปลี่ยน %d รายการ:" % len(changed))
    for c in changed:
        print("  - %s" % c)
    return 10


if __name__ == "__main__":
    sys.exit(main(sys.argv))
