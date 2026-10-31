import datetime as dt
import html
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


# ---- 商店來源：每個函式回傳 [{site,title,price,orig,url}]，要加平台就新增一個 ----
def pchome(p):
    r = requests.get(
        "https://ecshweb.pchome.com.tw/search/v3.3/all/results",
        params={"q": p["name"], "page": 1, "sort": "sale/dc"},
        headers=UA, timeout=20,
    )
    out = []
    for it in r.json().get("prods", []):
        out.append(dict(
            site="PChome 24h", title=it.get("name", ""),
            price=it.get("price"), orig=it.get("originPrice"),
            url=f"https://24h.pchome.com.tw/prod/{it.get('Id', '')}",
        ))
    return out


def serpapi(p):  # 選用：Google 購物，涵蓋 momo、蝦皮等；需設定 SERPAPI_KEY
    key = os.getenv("SERPAPI_KEY")
    if not key:
        return []
    r = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google_shopping", "q": p["name"], "gl": "tw",
                "hl": "zh-tw", "api_key": key},
        timeout=30,
    ).json()
    return [dict(
        site=i.get("source", "Google購物"), title=i.get("title", ""),
        price=i.get("extracted_price"), orig=i.get("extracted_old_price"),
        url=i.get("product_link") or i.get("link", ""),
    ) for i in r.get("shopping_results", [])]


ADAPTERS = [pchome, serpapi]


def ok(i, p):
    if not i.get("price") or i["price"] < p.get("min_price", 0):
        return False
    t = i["title"].lower()
    return (all(k.lower() in t for k in p.get("include", []))
            and not any(k.lower() in t for k in p.get("exclude", [])))


def notify(text):
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
              "disable_web_page_preview": "false"},
        timeout=20,
    )
    return r.ok


def render(products, state, now):
    rows = []
    for p in products:
        s = state.get(p["name"])
        if not s:
            rows.append(f"<article><h2>{html.escape(p['name'])}</h2><p class=note>尚無結果</p></article>")
            continue
        c, lo = s["current"], s["lowest"]
        orig = f"<span class=orig>原價 NT${c['orig']:,.0f}</span>" if c.get("orig") and c["orig"] > c["price"] else ""
        tag = "" if c.get("sale") else "<span class=note>目前無折扣標示</span>"
        rows.append(
            f"<article><h2>{html.escape(p['name'])}</h2>"
            f"<p class=price>NT${c['price']:,.0f}</p><p>{orig}{tag}</p>"
            f"<p>{html.escape(c['site'])}：{html.escape(c['title'][:60])}</p>"
            f"<p><a href=\"{html.escape(c['url'])}\">前往購買</a></p>"
            f"<p class=note>紀錄最低 NT${lo['price']:,.0f}，{html.escape(lo['site'])}，{lo['time']}</p></article>"
        )
    css = ("""
:root{--bg:#f3f6f8;--ink:#15212b;--mute:#5d6b77;--line:#d5dde3;--acc:#0b6e8a;--good:#1a7f4b}
@media(prefers-color-scheme:dark){:root{--bg:#10171d;--ink:#e6edf2;--mute:#93a1ad;--line:#26323c;--acc:#5cc0dc;--good:#5fd391}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "Noto Sans TC",system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:24px 20px 48px}
h1{font-size:1.4rem;margin:0}.meta{color:var(--mute);margin:4px 0 24px}
article{border-top:1px solid var(--line);padding:16px 0}
h2{font-size:1.05rem;margin:0}p{margin:4px 0}
.price{font-size:2rem;font-weight:700;color:var(--good)}
.orig{text-decoration:line-through;color:var(--mute);margin-right:8px}
.note{color:var(--mute);font-size:.9rem}a{color:var(--acc);font-weight:600}
""")
    return (f"<!doctype html><html lang=zh-Hant><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>比價追蹤</title><style>{css}</style><main><h1>比價追蹤</h1>"
            f"<p class=meta>每兩小時更新，最後更新 {now}</p>{''.join(rows)}</main></html>")


def main():
    if not (os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")):
        sys.exit("缺少 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
    products = json.loads((ROOT / "products.json").read_text("utf-8"))
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    for p in products:
        items = []
        for fn in ADAPTERS:
            try:
                items += fn(p)
            except Exception as e:
                print(f"[{p['name']}] {fn.__name__} 失敗：{e}")
        items = [i for i in items if ok(i, p)]
        if not items:
            print(f"[{p['name']}] 沒有符合的結果")
            continue
        sale = [i for i in items if i.get("orig") and i["orig"] > i["price"]]
        best = min(sale or items, key=lambda i: i["price"])
        best["sale"] = bool(sale)
        s = state.setdefault(p["name"], {})
        s["current"] = {**best, "time": now}
        prev = s.get("lowest")
        if prev is None or best["price"] < prev["price"]:
            old = f"\n上次最低：NT${prev['price']:,.0f}（{prev['site']}）" if prev else ""
            orig = f"（原價 NT${best['orig']:,.0f}）" if best.get("orig") else ""
            msg = (f"🔥 {p['name']}\n最低價 NT${best['price']:,.0f}{orig}\n"
                   f"商店：{best['site']}{old}\n{best['url']}")
            if notify(msg):
                s["lowest"] = {**best, "time": now}
            else:
                print(f"[{p['name']}] Telegram 發送失敗，下次再試")

    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)
    safe = {k: v for k, v in state.items() if "lowest" in v}
    (ROOT / "docs" / "index.html").write_text(render(products, safe, now), "utf-8")


if __name__ == "__main__":
    main()
