import urllib.request, gzip, http.cookiejar, time, json, re, sys

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CJ = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CJ))

HDR_A = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Cache-Control": "no-cache",
}
HDR_B = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
}

def do(url, headers, timeout=60):
    req = urllib.request.Request(url, headers=headers)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            body = raw.decode("utf-8", "replace")
            return r.status, len(body), len(re.findall(r"<item>|<entry>|SocialMediaPosting", body))
    except urllib.error.HTTPError as e:
        return e.code, 0, 0
    except Exception as e:
        return "ERR:" + type(e).__name__, 0, 0

print("=== PART 1: LinkedIn company 页大样本 A/B (N=8) ===")
URLS = ["https://www.linkedin.com/company/googledeepmind/",
        "https://www.linkedin.com/company/google/",
        "https://www.linkedin.com/showcase/google-cloud/"]
stats = {}
for i in range(8):
    for mode, hdr in (("A", HDR_A), ("B", HDR_B)):
        url = URLS[i % 3]
        st, size, items = do(url, hdr)
        key = mode + ":" + ("showcase" if "showcase" in url else url.split("/company/")[1].strip("/"))
        stats.setdefault(key, {"ok": 0, "fail": 0})
        stats[key]["ok" if st == 200 else "fail"] += 1
        print("[%d %s] %-16s http=%-5s bytes=%-7d items=%d" % (i, mode, url.split("/company/")[-1].strip("/") if "/company/" in url else "showcase", st, size, items), flush=True)
        time.sleep(2.0)
print("\n--- PART1 SUMMARY ---")
for k in sorted(stats): print("  %-28s ok=%d fail=%d" % (k, stats[k]["ok"], stats[k]["fail"]))

print("\n=== PART 2: 其他源副作用检查（完整头 B 是否破坏现有源）===")
OTHERS = [
    ("rss blog.google", "https://blog.google/rss/"),
    ("rss developers", "https://developers.googleblog.com/feeds/posts/default"),
    ("rss gcp-notes", "https://cloud.google.com/feeds/gcp-release-notes.xml"),
    ("registry ai.google.dev", "https://ai.google.dev/gemini-api/docs/changelog"),
    ("registry models", "https://ai.google.dev/gemini-api/docs/models"),
    ("registry cloud models", "https://cloud.google.com/vertex-ai/generative-ai/docs/models"),
    ("registry pricing", "https://ai.google.dev/gemini-api/docs/pricing"),
]
for label, url in OTHERS:
    a = do(url, HDR_A)
    time.sleep(1.0)
    b = do(url, HDR_B)
    flag = "SAME" if (a[0] == b[0] and abs(a[1] - b[1]) < max(2000, a[1]*0.15)) else "DIFF!!"
    print("  %-24s A(status=%s,bytes=%d,items=%d)  B(status=%s,bytes=%d,items=%d)  -> %s" % (label, a[0], a[1], a[2], b[0], b[1], b[2], flag), flush=True)
    time.sleep(1.0)
