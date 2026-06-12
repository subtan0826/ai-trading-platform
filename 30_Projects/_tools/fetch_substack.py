#!/usr/bin/env python3
"""
fetch_substack.py — pull FULL TEXT of paid Substack posts you are subscribed to,
using your own browser session cookie. Personal local archive ONLY (the library
folder is gitignored — paid content must never be pushed to GitHub).

Setup (once):
  1. Log in to the publication in your browser (e.g. newsletter.semianalysis.com).
  2. F12 -> Application -> Cookies -> https://newsletter.semianalysis.com
     copy the value of the session cookie:
       - on custom domains (newsletter.semianalysis.com) it is named `connect.sid`
       - on substack.com it is named `substack.sid`
  3. Put it in repo-root .env:   SUBSTACK_SID=<value>   (paste as-is, incl. s%3A prefix)

Usage:
  python fetch_substack.py <post_url> [<post_url> ...]      # fetch specific posts
  python fetch_substack.py --list [N]                       # list latest N archive posts (default 20)
  python fetch_substack.py --latest [N]                     # fetch latest N posts

Output: 50_Archive/research-library/<publication>/<date>_<slug>.md
"""
import os, re, sys, json, html, time, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]   # repo root
LIB = ROOT / "50_Archive" / "research-library"

def load_env():
    p = ROOT / ".env"
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def req(url, sid):
    # custom domains (e.g. newsletter.semianalysis.com) name the session cookie
    # `connect.sid`; substack.com names it `substack.sid` — send both, same value
    r = urllib.request.Request(url, headers={
        "Cookie": f"connect.sid={sid}; substack.sid={sid}",
        "User-Agent": "Mozilla/5.0 (personal-archive; subscriber)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def html_to_md(body_html):
    t = body_html
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", t, flags=re.S)
    t = re.sub(r"<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", t)
    t = re.sub(r"</h[1-6]>", "\n", t)
    t = re.sub(r"<li[^>]*>", "\n- ", t)
    t = re.sub(r"<(p|div|tr|br|blockquote)[^>]*>", "\n", t)
    t = re.sub(r"<img[^>]*src=\"([^\"]+)\"[^>]*>", r"\n![img](\1)\n", t)
    t = re.sub(r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"[\2](\1)", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def post_api(base, slug):
    return f"{base}/api/v1/posts/{slug}"

def fetch_post(url, sid):
    m = re.match(r"(https?://[^/]+)/p/([^/?#]+)", url.strip())
    if not m:
        print(f"!! 不是 Substack 文章链接: {url}")
        return None
    base, slug = m.group(1), m.group(2)
    data = json.loads(req(post_api(base, slug), sid))
    body = data.get("body_html") or ""
    paywalled = data.get("audience") == "only_paid" and not body
    md = html_to_md(body) if body else "(EMPTY — cookie 无效或无权限)"
    pub = re.sub(r"^www\.|\.com$|\.substack$", "", base.split("//")[1].split(".")[0])
    host = base.split("//")[1]
    pubdir = LIB / host
    pubdir.mkdir(parents=True, exist_ok=True)
    date = (data.get("post_date") or "")[:10] or "undated"
    out = pubdir / f"{date}_{slug}.md"
    front = (f"---\ntitle: \"{data.get('title','')}\"\nsubtitle: \"{(data.get('subtitle') or '')[:200]}\"\n"
             f"date: {date}\nurl: {url}\nwordcount: {len(md.split())}\n---\n\n")
    out.write_text(front + md, encoding="utf-8")
    flag = "⚠️ 可能不完整" if paywalled or len(md) < 1500 else "OK"
    print(f"  {flag}  {date}  {data.get('title','')[:70]}  -> {out.relative_to(ROOT)}  ({len(md.split())} words)")
    return out

def list_archive(base, sid, n=20):
    posts, offset = [], 0
    while len(posts) < n:
        batch = json.loads(req(f"{base}/api/v1/archive?sort=new&offset={offset}&limit=20", sid))
        if not batch:
            break
        posts += batch
        offset += len(batch)
    return posts[:n]

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows GBK console
    except Exception:
        pass
    load_env()
    sid = os.environ.get("SUBSTACK_SID")
    if not sid:
        sys.exit("缺 SUBSTACK_SID — 浏览器 F12 复制 substack.sid cookie 存入 .env")
    args = sys.argv[1:]
    base = os.environ.get("SUBSTACK_BASE", "https://newsletter.semianalysis.com")
    if not args:
        sys.exit(__doc__)
    if args[0] in ("--list", "--latest"):
        n = int(args[1]) if len(args) > 1 else 20
        posts = list_archive(base, sid, n)
        for p in posts:
            mark = "💰" if p.get("audience") == "only_paid" else "  "
            print(f"{mark} {p.get('post_date','')[:10]}  {p.get('title','')[:80]}")
            print(f"     {p.get('canonical_url','')}")
        if args[0] == "--latest":
            print("\n开始抓取全文…")
            for p in posts:
                fetch_post(p.get("canonical_url", ""), sid)
                time.sleep(2)          # 温和限速
    else:
        for url in args:
            fetch_post(url, sid)
            time.sleep(2)

if __name__ == "__main__":
    main()
