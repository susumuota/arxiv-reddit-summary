# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import dateutil.parser
import deeplcache
import generatehtml
import nanoatp
import pandas as pd
import pysbd
import utils


def generate_facets(text: str, patterns: list[tuple[str, str]]):
    # TODO: fix naive implementation
    facets: list[dict[str, Any]] = []
    for pattern, uri in patterns:
        start = text.find(pattern)
        if start == -1:
            continue
        end = start + len(pattern)
        facets.append(
            {
                "$type": "app.bsky.richtext.facet",
                "index": {"byteStart": start, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}],
            }
        )
    facets.sort(key=lambda facet: facet["index"]["byteStart"])
    return facets


def upload_first_page_to_bluesky(api: nanoatp.BskyAgent, arxiv_id: str, summary_text: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_filename = utils.download_arxiv_pdf(arxiv_id, tmp_dir)
        first_page_filename = utils.pdf_to_png(pdf_filename)
        if os.path.isfile(first_page_filename):
            return api.uploadImage(first_page_filename, summary_text)
    return {}


def generate_bluesky_first_page(df: pd.DataFrame, i: int, is_new: bool, arxiv_id: str, updated: str, title: str, summary_texts: list[str], authors: list[str], score: int, num_comments: int, count: int, primary_category: str, categories: list[str]):
    summary_text = " ".join(summary_texts)
    new_md = "🆕" if is_new else ""
    authors_md = ", ".join(authors)
    categories_md = utils.avoid_auto_link(" | ".join([primary_category] + [c for c in categories if c != primary_category and re.match(r"\w+\.\w+$", c)]))
    stats_md = f"{score} Likes, {num_comments} Comments, {count} Posts"
    updated_md = dateutil.parser.isoparse(updated).strftime("%d %b %Y")
    title_md = title
    text = f"[{len(df)-i}/{len(df)}] {stats_md}\n{arxiv_id}, {categories_md}, {updated_md}\n\n{new_md}{title_md}\n\n{authors_md}"
    return text, summary_text


def post_to_bluesky_first_page(api: nanoatp.BskyAgent, df: pd.DataFrame, i: int, is_new: bool, arxiv_id: str, updated: str, title: str, summary_texts: list[str], authors: list[str], score: int, num_comments: int, count: int, primary_category: str, categories: list[str]):
    first_page_text, summary_text = generate_bluesky_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
    images = []
    image = upload_first_page_to_bluesky(api, arxiv_id, utils.strip_tweet(summary_text, 300))
    images.append(image) if image else None
    parent_post: dict[str, str] = {}
    text = f"{first_page_text}"
    patterns = [(arxiv_id, f"https://arxiv.org/abs/{arxiv_id}")]
    facets = generate_facets(text, patterns)
    embed = {"$type": "app.bsky.embed.images#main", "images": images}
    record = {"text": utils.strip_tweet(text, 300), "facets": facets, "embed": embed}
    try:
        parent_post = api.post(record)
    except Exception as e:
        print(e)
    return parent_post


def post_to_bluesky_link(api: nanoatp.BskyAgent, root_post: dict[str, str], parent_post: dict[str, str], arxiv_id: str, title: str, summary_texts: list[str]):
    patterns = [
        ("abs", f"https://arxiv.org/abs/{arxiv_id}"),
        ("pdf", f"https://arxiv.org/pdf/{arxiv_id}.pdf"),
        ("Twitter", f"https://twitter.com/search?q=arxiv.org%2Fabs%2F{arxiv_id}%20OR%20arxiv.org%2Fpdf%2F{arxiv_id}.pdf"),
        ("Reddit", f"https://www.reddit.com/search/?q=%22{arxiv_id}%22&sort=top"),
        ("Hacker News", f"https://hn.algolia.com/?query=%22{arxiv_id}%22&type=all"),
    ]
    text = "Links: abs, pdf\nSearch: Twitter, Reddit, Hacker News"
    facets = generate_facets(text, patterns)
    external = {"$type": "app.bsky.embed.external#external", "uri": patterns[0][1], "title": title, "description": utils.strip_tweet(" ".join(summary_texts), 300)}
    embed = {"$type": "app.bsky.embed.external#main", "external": external}
    record = {"text": utils.strip_tweet(text, 300), "facets": facets, "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
    try:
        parent_post = api.post(record)
    except Exception as e:
        print(e)
    return parent_post


def post_to_bluesky_posts(api: nanoatp.BskyAgent, root_post: dict[str, str], parent_post: dict[str, str], df: pd.DataFrame):
    for i, (id, score, num_comments, created_at, title, description) in enumerate(zip(df["id"], df["score"], df["num_comments"], df["created_at"], df["title"], df["description"])):
        stats_md = f"{score} Likes, {num_comments} Comments"
        created_at_md = datetime.fromtimestamp(created_at).strftime("%d %b %Y")
        link = "Reddit" if id.find("reddit.com") != -1 else "Hacker News" if id.find("news.ycombinator.com") != -1 else id
        text = f"({i+1}/{len(df)}) {stats_md}, {created_at_md}, {link}"
        patterns = [(link, id)]
        facets = generate_facets(text, patterns)
        external = {"$type": "app.bsky.embed.external#external", "uri": id, "title": title, "description": description}
        embed = {"$type": "app.bsky.embed.external#main", "external": external}
        record = {"text": utils.strip_tweet(text, 300), "facets": facets, "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
        try:
            parent_post = api.post(record)
        except Exception as e:
            print(e)
        time.sleep(1)
    return parent_post


def upload_html_to_bluesky(api: nanoatp.BskyAgent, filename: str, html_text: str, alt_text: str, quality: int = 94) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        abs_path = os.path.join(tmp_dir, filename)
        abs_path = utils.html_to_image(html_text, abs_path, quality)
        if os.path.isfile(abs_path):
            return api.uploadImage(abs_path, alt_text)
    return {}


def post_to_bluesky_trans(api: nanoatp.BskyAgent, root_post: dict[str, str], parent_post: dict[str, str], arxiv_id: str, title: str, authors: list[str], summary_texts: list[str], trans_texts: list[str]) -> dict[str, str]:
    html_text = generatehtml.generate_trans_html(arxiv_id, title, authors, trans_texts, summary_texts)
    trans_text = "".join(trans_texts)
    images = []
    image = upload_html_to_bluesky(api, f"{arxiv_id}.trans.jpg", html_text, utils.strip_tweet(trans_text, 300))
    images.append(image) if image else None
    text = f"{arxiv_id}\n{trans_text}"
    patterns = [(arxiv_id, f"https://arxiv.org/abs/{arxiv_id}")]
    facets = generate_facets(text, patterns)
    embed = {"$type": "app.bsky.embed.images#main", "images": images}
    record = {"text": utils.strip_tweet(text, 300), "facets": facets, "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
    try:
        return api.post(record)
    except Exception as e:
        print(e)
    return {}


def post_to_bluesky_ranking(api: nanoatp.BskyAgent, dlc: deeplcache.DeepLCache, df: pd.DataFrame) -> dict[str, str]:
    title = f"Top {len(df)} most popular arXiv papers in the last 30 days.\n"
    date = datetime.now(timezone.utc).strftime("%d %b %Y")
    html_text = generatehtml.generate_top_n_html(title, date, df, dlc)
    uris = list(map(lambda item: (f"{item[0]+1}/{len(df)}", f"https://arxiv.org/abs/{item[1][0]}"), enumerate(zip(df[::-1]["arxiv_id"]))))
    alt_text = "\n".join(map(lambda item: " ".join(item), uris))
    image = upload_html_to_bluesky(api, "top_n.jpg", html_text, utils.strip_tweet(alt_text, 300), 90)  # sometimes the image is too large to upload
    images = []
    images.append(image) if image else None
    text = title + " ".join(map(lambda item: f"[{item[0]}]", uris))
    facets = generate_facets(text, uris)
    embed = {"$type": "app.bsky.embed.images#main", "images": images}
    record = {"text": utils.strip_tweet(text, 300), "facets": facets, "embed": embed}
    try:
        return api.post(record)
    except Exception as e:
        print(e)
    return {}


def post_to_bluesky(api: nanoatp.BskyAgent, dlc: deeplcache.DeepLCache, df: pd.DataFrame, document_df: pd.DataFrame):
    df = df[::-1]  # reverse order
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    seg = pysbd.Segmenter(language="en", clean=False)
    for i, (arxiv_id, updated, title, summary, authors, primary_category, categories, score, num_comments, count) in enumerate(zip(df["arxiv_id"], df["updated"], df["title"], df["summary"], df["authors"], df["primary_category"], df["categories"], df["score"], df["num_comments"], df["count"])):
        trans = dlc.get(arxiv_id, None)
        if trans is None:
            continue
        trans_texts, trans_ts = trans
        # only post new papers
        if not (twenty_three_hours_ago < datetime.fromisoformat(trans_ts)):
            continue
        segs = seg.segment(summary.replace("\n", " ")[:2000])
        summary_texts: list[str] = [str(seg) for seg in segs] if type(segs) is list else [segs] if type(segs) is str else []
        is_new = True
        parent_post = post_to_bluesky_first_page(api, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
        if parent_post is None:
            continue
        root_post = parent_post
        time.sleep(1)
        top_n_documents = document_df[document_df["arxiv_id"].apply(lambda ids: arxiv_id in ids)].head(3)  # TODO
        parent_post = post_to_bluesky_posts(api, root_post, parent_post, top_n_documents)
        parent_post = post_to_bluesky_link(api, root_post, parent_post, arxiv_id, title, summary_texts)
        time.sleep(1)
        post_to_bluesky_trans(api, root_post, parent_post, arxiv_id, title, authors, summary_texts, trans_texts)
        print("post_to_bluesky: ", f"[{len(df)-i}/{len(df)}]")
        time.sleep(1)
    return post_to_bluesky_ranking(api, dlc, df)
