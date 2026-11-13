"""Bygger Prisjakt-flödet (Google Shopping-format, samma som Prisjakt redan läser)
från alla aktiva, publicerade produkter i lager på byggoverskott.se.

Kör:  python feed.py               -> hämtar från Shopify och skriver site/prisjakt.xml
      python feed.py --from x.json -> bygger från en sparad produktlista (för test)
"""
import collections, html, json, os, re, sys, time, urllib.parse, urllib.request
from xml.sax.saxutils import escape

SHOP_URL = "https://www.byggoverskott.se"
OUT = os.path.join("site", "prisjakt.xml")
API_VERSION = "2026-10"
MIN_PRICE = 250  # kr inkl. moms; billigare produkter tas inte med (klickkostnaden äter marginalen)

# ---------- Shopify ----------
E = os.environ
_tok = {}


def token():
    if _tok.get("exp", 0) > time.time() + 600:
        return _tok["tok"]
    d = urllib.parse.urlencode({"grant_type": "client_credentials",
                                "client_id": E["SHOPIFY_CLIENT_ID"],
                                "client_secret": E["SHOPIFY_CLIENT_SECRET"]}).encode()
    r = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://{E['SHOPIFY_SHOP']}.myshopify.com/admin/oauth/access_token", data=d), timeout=30).read())
    _tok.update(tok=r["access_token"], exp=time.time() + r["expires_in"])
    return _tok["tok"]


def gql(q, v=None):
    for _ in range(6):
        req = urllib.request.Request(
            f"https://{E['SHOPIFY_SHOP']}.myshopify.com/admin/api/{API_VERSION}/graphql.json",
            data=json.dumps({"query": q, "variables": v or {}}).encode(),
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token()})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        if "errors" in r and "THROTTLED" in json.dumps(r["errors"]):
            time.sleep(3)
            continue
        if "errors" in r:
            raise RuntimeError(json.dumps(r["errors"])[:500])
        ts = r.get("extensions", {}).get("cost", {}).get("throttleStatus", {})
        if ts and ts.get("currentlyAvailable", 1000) < 300:
            time.sleep(2)
        return r["data"]
    raise RuntimeError("Shopify throttlade för många gånger")


Q = """query($c:String){ products(first:50, after:$c, query:"status:active"){ pageInfo{hasNextPage endCursor} nodes{
 legacyResourceId title handle productType vendor tags descriptionHtml onlineStoreUrl
 media(first:10){ nodes{ ... on MediaImage { image { url } } } }
 variants(first:100){ nodes{ legacyResourceId title price compareAtPrice availableForSale inventoryQuantity
   barcodes(first:3){ nodes{ value } } inventoryItem{ measurement{ weight{ value unit } } } } } } } }"""


def fetch_products():
    out, cur = [], None
    while True:
        d = gql(Q, {"c": cur})["products"]
        out += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            return out
        cur = d["pageInfo"]["endCursor"]


# ---------- Regler ----------
def valid_gtin(s):
    s = re.sub(r"\D", "", s or "")
    if len(s) not in (8, 12, 13, 14) or set(s) == {"0"}:
        return None
    digits = [int(c) for c in s]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    return s if (10 - total % 10) % 10 == digits[-1] else None


def kg(variant):
    w = ((variant.get("inventoryItem") or {}).get("measurement") or {}).get("weight") or {}
    v = w.get("value") or 0
    f = {"KILOGRAMS": 1, "GRAMS": 0.001, "POUNDS": 0.4536, "OUNCES": 0.02835}.get(w.get("unit"), 1)
    return round(v * f, 2)


def money(x):
    return f"{float(x):.2f} SEK"


def text(desc_html, limit=5000):
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", desc_html or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t).strip()
    return t[:limit]


def brand_key(v):
    return re.sub(r"[^a-z0-9åäö]", "", v.lower())


def canonical_brands(products):
    """Samma märke stavat olika (ESSVE/Essve, Millers/Miller's) -> vanligaste stavningen."""
    seen = collections.defaultdict(collections.Counter)
    for p in products:
        v = clean_vendor(p.get("vendor"))
        if v:
            seen[brand_key(v)][v] += 1
    return {k: c.most_common(1)[0][0] for k, c in seen.items()}


def clean_vendor(v):
    v = html.unescape((v or "").strip())
    if "byggöverskott" in v.lower():
        return "Byggöverskott"
    return v


def tags_lower(p):
    return {t.lower() for t in p.get("tags") or []}


def build_items(products):
    brands = canonical_brands(products)
    items, skipped = [], collections.Counter()
    for p in products:
        tags = tags_lower(p)
        if not p.get("onlineStoreUrl"):
            skipped["inte publicerad i webbutiken"] += 1
            continue
        if "endast-butik" in tags:
            skipped["endast-butik (bara upphämtning)"] += 1
            continue
        variants = p["variants"]["nodes"]
        imgs = [m["image"]["url"] for m in p["media"]["nodes"] if m and m.get("image")]
        if not imgs:
            skipped["saknar bild"] += 1
            continue
        vendor = clean_vendor(p.get("vendor"))
        brand = brands.get(brand_key(vendor), vendor) if vendor else ""
        multi = len(variants) > 1
        for v in variants:
            if not v.get("availableForSale") or (v.get("inventoryQuantity") or 0) <= 0:
                skipped["slut i lager"] += 1
                continue
            price = float(v["price"])
            if price <= 0:
                skipped["pris 0"] += 1
                continue
            if price < MIN_PRICE:
                skipped[f"pris under {MIN_PRICE} kr"] += 1
                continue
            cmp = float(v["compareAtPrice"]) if v.get("compareAtPrice") else 0
            gtin = next((g for g in (valid_gtin(b["value"]) for b in v["barcodes"]["nodes"]) if g), None)
            title = p["title"].strip()
            if multi and v["title"] != "Default Title":
                title = f"{title} - {v['title']}"
            items.append({
                # En-variantsprodukter behåller produkt-ID:t som LitCommerce använde,
                # så att Prisjakts befintliga matchningar ligger kvar.
                "id": v["legacyResourceId"] if multi else p["legacyResourceId"],
                "group": p["legacyResourceId"] if multi else None,
                "title": title[:150],
                "description": text(p.get("descriptionHtml")) or title,
                "link": f"{SHOP_URL}/products/{p['handle']}" + (f"?variant={v['legacyResourceId']}" if multi else ""),
                "image": imgs[0],
                "extra_images": imgs[1:10],
                "price": money(cmp if cmp > price else price),
                "sale_price": money(price) if cmp > price else None,
                "availability": "in_stock",
                "condition": "used" if "återbruk" in tags else "new",
                "brand": brand,
                "gtin": gtin,
                "product_type": p.get("productType") or "",
                "weight": kg(v),
            })
    return items, skipped


def to_xml(items):
    def el(tag, val):
        return f"<g:{tag}>{escape(str(val))}</g:{tag}>" if val not in (None, "") else ""

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>',
           "<title>Byggöverskott.se</title>", f"<link>{SHOP_URL}</link>",
           "<description>Produktflöde för Prisjakt</description>",
           f"<pubDate>{time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())}</pubDate>"]
    for it in items:
        parts = [el("id", it["id"]), el("item_group_id", it["group"]), el("title", it["title"]),
                 el("description", it["description"]), el("link", it["link"]), el("image_link", it["image"])]
        parts += [el("additional_image_link", u) for u in it["extra_images"]]
        parts += [el("price", it["price"]), el("sale_price", it["sale_price"]),
                  el("availability", it["availability"]), el("condition", it["condition"]),
                  el("brand", it["brand"]), el("gtin", it["gtin"]),
                  el("identifier_exists", "no" if not it["gtin"] else None),
                  el("product_type", it["product_type"]),
                  el("google_product_category", "Hardware > Building Materials"),
                  el("shipping_weight", f"{it['weight']} kg" if it["weight"] > 0 else None)]
        out.append("<item>" + "".join(parts) + "</item>")
    out.append("</channel></rss>")
    return "\n".join(out)


def main():
    if "--from" in sys.argv:
        products = json.load(open(sys.argv[sys.argv.index("--from") + 1]))
    else:
        products = fetch_products()
    items, skipped = build_items(products)
    os.makedirs("site", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(to_xml(items))
    with_gtin = sum(1 for i in items if i["gtin"])
    summary = {"aktiva_produkter": len(products), "i_flodet": len(items), "med_ean": with_gtin,
               "bortvalda": dict(skipped), "byggt": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    with open(os.path.join("site", "status.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if not items:
        sys.exit("Flödet blev tomt - publicerar inte.")


if __name__ == "__main__":
    main()
