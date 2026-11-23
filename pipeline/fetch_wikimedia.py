"""Commons image licenses and Wikipedia lead sections for mammals.

Commons: for each Wikidata P18 image, read license, author and credit from
imageinfo/extmetadata (50 files per call). Files without a recognisable open
license are dropped.

Wikipedia: plain-text lead section (exintro) for each English article,
20 per call. Stored verbatim with revision id so the site can attribute it
as CC BY-SA 4.0 and link to the exact version used.

Writes data/media/commons_images.parquet and data/text/wikipedia_leads.parquet.
"""
from __future__ import annotations

import html
import json
import re

import pandas as pd

from common import CACHE, CONFIG, DATA, Http, chunks

COMMONS = "https://commons.wikimedia.org/w/api.php"
ENWIKI = "https://en.wikipedia.org/w/api.php"
TAG = re.compile(r"<[^>]+>")


def clean(s: str | None) -> str | None:
    if not s:
        return None
    return html.unescape(TAG.sub("", s)).strip() or None


def norm_license(short: str | None, url: str | None) -> str | None:
    s = (short or "").strip().lower()
    if not s:
        return None
    if s in ("public domain", "pd", "cc0", "cc0 1.0", "no restrictions") or s.startswith("pd-") or s.startswith("pd "):
        return "pd" if "cc0" not in s else "cc0"
    s = s.replace(" ", "-")
    if s.startswith("cc-by"):
        return s
    return None


def cached_query(http: Http, url: str, kind: str, i: int, params: dict) -> dict:
    """One API call, cached on disk so a throttled run resumes where it stopped."""
    path = CACHE / "wikimedia" / kind / f"{i:05d}.json"
    if path.exists():
        return json.loads(path.read_text())
    r = http.json(url, params=params)
    if "query" not in r:
        raise RuntimeError(f"{kind} batch {i}: unexpected response {str(r)[:300]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(r))
    return r


def commons(http: Http, files: list[str]) -> list[dict]:
    rows = []
    for i, batch in enumerate(chunks(files, 50)):
        r = cached_query(http, COMMONS, "commons", i, {
            "action": "query", "format": "json", "formatversion": 2, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 960,
            "titles": "|".join("File:" + f for f in batch)})
        for page in r["query"]["pages"]:
            ii = (page.get("imageinfo") or [None])[0]
            if not ii:
                continue
            md = ii.get("extmetadata", {})
            get = lambda k: (md.get(k) or {}).get("value")
            lic = norm_license(get("LicenseShortName"), get("LicenseUrl"))
            rows.append({
                "file": page["title"].removeprefix("File:"),
                "license": lic, "license_url": get("LicenseUrl"),
                "artist": clean(get("Artist")), "credit": clean(get("Credit")),
                "description": clean(get("ImageDescription")),
                "thumb_960": ii.get("thumburl"), "page": ii.get("descriptionurl"),
                "width": ii.get("width"), "height": ii.get("height"),
                "mime": ii.get("mime"),
            })
    return rows


def leads(http: Http, titles: list[str]) -> list[dict]:
    rows = []
    for i, batch in enumerate(chunks(titles, 20)):
        if i % 25 == 0:
            print(f"  wikipedia batch {i}/{(len(titles) + 19) // 20}", flush=True)
        r = cached_query(http, ENWIKI, "enwiki", i, {
            "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
            "prop": "extracts|revisions|pageprops", "exintro": 1, "explaintext": 1,
            "rvprop": "ids|timestamp", "ppprop": "wikibase_item",
            "titles": "|".join(batch)})
        q = r["query"]
        back = {t["to"]: t["from"] for t in q.get("redirects", [])}
        back.update({t["to"]: t["from"] for t in q.get("normalized", [])})
        for page in q["pages"]:
            if page.get("missing") or not page.get("extract"):
                continue
            rev = (page.get("revisions") or [{}])[0]
            asked = back.get(page["title"], page["title"])
            rows.append({
                "enwiki": back.get(asked, asked), "title": page["title"],
                "qid": (page.get("pageprops") or {}).get("wikibase_item"),
                "lead": page["extract"].strip(), "revid": rev.get("revid"),
                "rev_timestamp": rev.get("timestamp"),
            })
    return rows


def main() -> None:
    xw = pd.read_parquet(DATA / "crosswalk" / "mammals_wikidata.parquet")

    http_c = Http(min_interval=CONFIG["rate"]["commons"])
    files = sorted(xw["commons_image"].dropna().unique())
    imgs = pd.DataFrame(commons(http_c, files))
    imgs = imgs[imgs["mime"].isin(["image/jpeg", "image/png", "image/webp"])]
    (DATA / "media").mkdir(parents=True, exist_ok=True)
    imgs.to_parquet(DATA / "media" / "commons_images.parquet", index=False)
    print(f"Commons: {len(files)} files, {imgs['license'].notna().sum()} with an open license")

    http_w = Http(min_interval=CONFIG["rate"]["wikipedia"])
    titles = sorted(xw["enwiki"].dropna().unique())
    ld = pd.DataFrame(leads(http_w, titles))
    (DATA / "text").mkdir(parents=True, exist_ok=True)
    ld.to_parquet(DATA / "text" / "wikipedia_leads.parquet", index=False)
    print(f"Wikipedia: {len(ld)} lead sections from {len(titles)} titles; "
          f"median {int(ld['lead'].str.len().median())} characters")


if __name__ == "__main__":
    main()
