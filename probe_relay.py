# -*- coding: utf-8 -*-
"""Live check: provider through the relay + does the relay keep <script id="jsdata">."""
import asyncio
import os
import re
import time

os.environ["KRISHA_FETCH_RELAY"] = "https://r.jina.ai/"

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.providers.krisha_html import KrishaHtmlProvider  # noqa: E402
from app.search.filters import SearchQuery  # noqa: E402


async def main() -> None:
    settings = Settings()
    provider = KrishaHtmlProvider(settings)
    query = SearchQuery(city="Алматы", rooms=2)
    t0 = time.monotonic()
    items = await provider.search_paged(query, 1, 20)
    print(f"search page1: {len(items)} items in {time.monotonic() - t0:.1f}s")
    for it in items[:5]:
        print(f"  id={it.external_id} price={it.price} rooms={it.rooms} area={it.area} district={it.district}")
        print(f"     title={it.title[:70]}")

    if items:
        t1 = time.monotonic()
        full = await provider.fetch_detail(items[0])
        print(f"detail: {time.monotonic() - t1:.1f}s source={(full.details or {}).get('description_source')} len={len(full.description or '')}")

    # raw probe: what does the relay actually hand back for a detail page?
    url = "https://r.jina.ai/" + items[0].url
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"x-respond-with": "html"})
    body = r.text
    scripts = len(re.findall(r"<script", body))
    has_jsdata = 'id="jsdata"' in body
    has_h1 = "<h1" in body
    print(f"raw detail via relay: {r.status_code} len={len(body)} jsdata={has_jsdata} scripts={scripts} h1={has_h1}")
    m = re.search(r'id="jsdata"[^>]*>(.{0,200})', body, re.S)
    print("jsdata head:", (m.group(1)[:160] if m else "NOT FOUND"))


asyncio.run(main())
