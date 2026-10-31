import datetime as dt
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).parent
STATE = ROOT / "data" / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
PLATFORMS = ["PChome", "momo", "蝦皮", "Coupang", "Yahoo"]
SEARCH = {
    "PChome": "https://24h.pchome.com.tw/search/?q=",
    "momo": "https://www.momoshop.com.tw/search/searchShop.jsp?keyword=",
    "蝦皮": "https://shopee.tw/search?keyword=",
    "Coupang": "https://www.tw.coupang.com/np/search?q=",
    "Yahoo": "https://tw.buy.yahoo.com/search/product?p=",
}
KEYS = (("pchome", "PChome"), ("momo", "momo"), ("蝦皮", "蝦皮"), ("shopee", "蝦皮"),
        ("coupang", "Coupang"), ("酷澎", "Coupang"), ("yahoo", "Yahoo"), ("奇摩", "Yahoo"))


def num(v):
    d = re.sub(r"[^\d.]", "", str(v or ""))
    return float(d) if d else None


def norm(site):
    s = site.lower()
    for k, n in KEYS:
        if k in s:
            return n
    return site


def query(p):
    return p.get("query") or re.sub(r"[()（）]", " ", p["name"]).strip()


# ---- 直連各平台（在 GitHub 的 IP 上常被擋，失敗會顯示在頁面上） ----
def pchome(p):
    r = requests.get("https://ecshweb.pchome.com.tw/search/v3.3/all/results",
                     params={"q": query(p), "page": 1, "sort": "sale/dc"}, headers=UA, timeout=20)
    r.raise_for_status()
    return [dict(site="PChome", title=i.get("name", ""), price=num(i.get("price")),
                 orig=num(i.get("originPrice")), url=f"https://24h.pchome.com.tw/prod/{i.get('Id', '')}")
            for i in r.json().get("prods", [])]


def momo(p):
    q = query(p)
    body = {"host": "momoshop", "flag": "searchEngine", "data": {
        "searchValue": q, "searchType": "1", "curPage": "1", "priceS": "0", "priceE": "9999999",
        "cateCode": "", "cateLv": "-1", "cateLevel": "-1", "maxPage": "1", "isFuzzy": "0",
        "rtnCateDatainfo": {"cateCode": "", "cateLv": "-1", "keyword": q, "curPage": "1",
                            "historyDoPush": False, "timestamp": int(time.time() * 1000)},
        "flag": "all", "_advCp": "N", "_advFirst": "N", "_advFreeze": "N", "_advTvShop": "N",
        "_advStock": "N", "_advSuperstore": "N"}}
    r = requests.post("https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch",
                      json=body, headers={**UA, "Referer": "https://www.momoshop.com.tw/"}, timeout=20)
    r.raise_for_status()
    goods = (r.json().get("rtnSearchData") or {}).get("goodsInfoList") or []
    return [dict(site="momo", title=g.get("goodsName", ""),
                 price=num(g.get("goodsPrice") or g.get("SALE_PRICE")), orig=num(g.get("MARKET_PRICE")),
                 url=f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={g.get('goodsCode', '')}")
            for g in goods]


def shopee(p):
    r = requests.get("https://shopee.tw/api/v4/search/search_items",
                     params={"by": "relevancy", "keyword": query(p), "limit": 30, "newest": 0,
                             "order": "desc", "page_type": "search", "scenario": "PAGE_GLOBAL_SEARCH",
                             "version": 2},
                     headers={**UA, "Referer": "https://shopee.tw/"}, timeout=20)
    r.raise_for_status()
    items = r.json().get("items") or []
    if not items:
        raise RuntimeError("被擋或沒有資料")
    out = []
    for it in items:
        b = it.get("item_basic", {})
        out.append(dict(site="蝦皮", title=b.get("name", ""), price=(b.get("price") or 0) / 1e5,
                        orig=(b.get("price_before_discount") or 0) / 1e5,
                        url=f"https://shopee.tw/product/{b.get('shopid')}/{b.get('itemid')}"))
    return out


def coupang(p):
    r = requests.get("https://www.tw.coupang.com/np/search", params={"q": query(p)}, headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'<li[^>]*class="[^"]*search-product[^"]*"[^>]*>(.*?)</li>', r.text, re.S):
        b = m.group(1)
        n = re.search(r'class="name"[^>]*>(.*?)</div>', b, re.S)
        pr = re.search(r'class="price-value"[^>]*>\s*([\d,]+)', b)
        h = re.search(r'href="(/vp/products/[^"]+)"', b)
        if n and pr and h:
            out.append(dict(site="Coupang", title=re.sub(r"<[^>]+>|\s+", " ", n.group(1)).strip(),
                            price=num(pr.group(1)), orig=None, url="https://www.tw.coupang.com" + html.unescape(h.group(1))))
    return out


def serpapi(p):  # Google 購物：同時涵蓋 momo、蝦皮、Coupang、Yahoo；需 SERPAPI_KEY
    r = requests.get("https://serpapi.com/search.json",
                     params={"engine": "google_shopping", "q": query(p), "gl": "tw", "hl": "zh-tw",
                             "api_key": os.environ["SERPAPI_KEY"]}, timeout=30)
    r.raise_for_status()
    return [dict(site=norm(i.get("source", "Google購物")), title=i.get("title", ""),
                 price=i.get("extracted_price"), orig=i.get("extracted_old_price"),
                 url=i.get("product_link") or i.get("link", ""))
            for i in r.json().get("shopping_results", [])]


ADAPTERS = [(pchome, "PChome"), (momo, "momo"), (shopee, "蝦皮"), (coupang, "Coupang")]
if os.getenv("SERPAPI_KEY"):
    ADAPTERS.append((serpapi, None))


def ok(i, p):
    if not i.get("price") or i["price"] < p.get("min_price", 0):
        return False
    t = i["title"].lower()
    return (all(k.lower() in t for k in p.get("include", []))
            and not any(k.lower() in t for k in p.get("exclude", [])))


def pick(items):
    sale = [i for i in items if i.get("orig") and i["orig"] > i["price"]]
    b = dict(min(sale or items, key=lambda i: i["price"]))
    b["sale"] = bool(sale)
    return b


def collect(p):
    raw, notes = [], {k: [] for k in PLATFORMS}
    if not os.getenv("SERPAPI_KEY"):
        notes["Yahoo"].append("未設定 SERPAPI_KEY，Yahoo 無法查詢")
    for fn, pl in ADAPTERS:
        try:
            got = [i for i in fn(p) if i.get("price")]
        except Exception as e:
            notes.setdefault(pl or "Google購物", []).append(f"{fn.__name__}失敗：{str(e)[:50]}")
            continue
        raw += got
        if pl:
            notes[pl].append(f"直連 {len(got)} 筆")
        else:
            for k in {i["site"] for i in got}:
                notes.setdefault(k, []).append(f"Google購物 {sum(i['site'] == k for i in got)} 筆")
    plats = {}
    for site in PLATFORMS + sorted({i["site"] for i in raw} - set(PLATFORMS)):
        mine = [i for i in raw if i["site"] == site]
        hit = [i for i in mine if ok(i, p)]
        note = "，".join(notes.get(site, [])) or "無資料"
        plats[site] = {"best": pick(hit) if hit else None,
                       "note": f"{note}，符合條件 {len(hit)} 筆"}
    return plats


def notify(text):
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
                      data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}, timeout=20)
    return r.ok


CSS = """
:root{--bg:#f3f6f8;--ink:#15212b;--mute:#5d6b77;--line:#d5dde3;--acc:#0b6e8a;--good:#1a7f4b}
@media(prefers-color-scheme:dark){:root{--bg:#10171d;--ink:#e6edf2;--mute:#93a1ad;--line:#26323c;--acc:#5cc0dc;--good:#5fd391}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "Noto Sans TC",system-ui,sans-serif}
main{max-width:680px;margin:0 auto;padding:24px 20px 48px}
h1{font-size:1.4rem;margin:0}.meta{color:var(--mute);margin:4px 0 24px}
article{border-top:1px solid var(--line);padding:16px 0}
h2{font-size:1.1rem;margin:0 0 4px}p{margin:4px 0}
.low{font-size:1.8rem;font-weight:700;color:var(--good);margin:0}
.row{padding:8px 0;border-top:1px dashed var(--line)}
.pp{font-weight:700;color:var(--good);margin:0 6px}.orig{text-decoration:line-through;color:var(--mute);margin-right:6px}
.note{color:var(--mute);font-size:.88rem}a{color:var(--acc);font-weight:600}
"""


def row(site, d, q):
    b = d.get("best")
    if b:
        o = f"<span class=orig>NT${b['orig']:,.0f}</span>" if b.get("orig") and b["orig"] > b["price"] else ""
        return (f"<div class=row><b>{html.escape(site)}</b><span class=pp>NT${b['price']:,.0f}</span>{o}"
                f"<a href=\"{html.escape(b['url'])}\">前往購買</a>"
                f"<div class=note>{html.escape(b['title'][:70])}</div></div>")
    s = SEARCH.get(site)
    link = f" <a href=\"{s + quote(q)}\">自行搜尋</a>" if s else ""
    return f"<div class=row><b>{html.escape(site)}</b> <span class=note>無結果：{html.escape(d['note'])}</span>{link}</div>"


def render(products, state, now):
    out = []
    for p in products:
        s = state.get(p["name"], {})
        low = s.get("lowest")
        head = (f"<p class=low>NT${low['price']:,.0f}</p><p class=note>紀錄最低：{html.escape(low['site'])}，{low['time']}</p>"
                if low else "<p class=note>尚未找到符合條件的商品</p>")
        rows = "".join(row(k, v, query(p)) for k, v in s.get("platforms", {}).items())
        out.append(f"<article><h2>{html.escape(p['name'])}</h2>{head}{rows}</article>")
    return (f"<!doctype html><html lang=zh-Hant><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>比價追蹤</title>"
            f"<style>{CSS}</style><main><h1>比價追蹤</h1>"
            f"<p class=meta>每兩小時更新，最後更新 {now}</p>{''.join(out)}</main></html>")


def main():
    if not (os.getenv("TELEGRAM_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")):
        sys.exit("缺少 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
    products = json.loads((ROOT / "products.json").read_text("utf-8"))
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    for p in products:
        plats = collect(p)
        s = state.setdefault(p["name"], {})
        s["platforms"] = plats
        for k, v in plats.items():
            print(f"[{p['name']}] {k}: {v['note']}" + (f" 最低 {v['best']['price']:,.0f}" if v["best"] else ""))
        bests = [v["best"] for v in plats.values() if v["best"]]
        if not bests:
            continue
        best = pick(bests)
        prev = s.get("lowest")
        if prev is None or best["price"] < prev["price"]:
            old = f"\n上次最低：NT${prev['price']:,.0f}（{prev['site']}）" if prev else ""
            orig = f"（原價 NT${best['orig']:,.0f}）" if best.get("orig") else ""
            lines = "\n".join(f"{k}：NT${v['best']['price']:,.0f}" for k, v in plats.items() if v["best"])
            msg = (f"🔥 {p['name']}\n最低價 NT${best['price']:,.0f}{orig}\n商店：{best['site']}{old}\n"
                   f"{best['url']}\n\n各平台：\n{lines}")
            if notify(msg):
                s["lowest"] = {**best, "time": now}
            else:
                print(f"[{p['name']}] Telegram 發送失敗，下次再試")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(render(products, state, now), "utf-8")


if __name__ == "__main__":
    main()
