# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import nanoatp
import pysbd

import generatehtml
import utils


def upload_first_page_to_bluesky(api: nanoatp.BskyAgent, arxiv_id, summary_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_filename = utils.download_arxiv_pdf(arxiv_id, tmp_dir)
        first_page_filename = utils.pdf_to_png(pdf_filename)
        if os.path.isfile(first_page_filename):
            return api.uploadImage(first_page_filename, summary_text)
    return None


def post_to_bluesky_first_page(api: nanoatp.BskyAgent, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories):
    text, summary_text = utils.generate_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
    images = []
    image = upload_first_page_to_bluesky(api, arxiv_id, utils.strip_tweet(summary_text, 300))
    images.append(image) if image else None
    parent_post = None
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": utils.strip_tweet(text, 300), "embed": embed}
        rt = nanoatp.RichText(record["text"])
        rt.detectFacets(api)
        record.update({"facets": rt.facets}) if len(rt.facets) > 0 else None
        parent_post = api.post(record)
    except Exception as e:
        print(e)
    return parent_post


def post_to_bluesky_link(api: nanoatp.BskyAgent, root_post, parent_post, arxiv_id, title):
    uri = f"https://twitter.com/search?q=arxiv.org%2Fabs%2F{arxiv_id}"
    text = f"Twitter Search: {uri}"
    try:
        external = {"$type": "app.bsky.embed.external#external", "uri": uri, "title": "Twitter Search", "description": title}
        embed = {"$type": "app.bsky.embed.external#main", "external": external}
        record = {"text": utils.strip_tweet(text, 300), "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
        rt = nanoatp.RichText(record["text"])
        rt.detectFacets(api)
        record.update({"facets": rt.facets}) if len(rt.facets) > 0 else None
        parent_post = api.post(record)
    except Exception as e:
        print(e)
    return parent_post


def post_to_bluesky_posts(api: nanoatp.BskyAgent, root_post, parent_post, arxiv_id, df):
    for i, (id, score, num_comments, created_utc, title, selftext) in enumerate(zip(df["id"], df["score"], df["num_comments"], df["created_utc"], df["title"], df["selftext"])):
        stats_md = f"{score} Upvotes, {num_comments} Comments"
        created_at_md = datetime.fromtimestamp(created_utc).strftime("%d %b %Y")
        url_md = f"https://reddit.com/{id}"
        text = f"({i+1}/{len(df)}) {stats_md}, {created_at_md}\n{url_md}\n"
        try:
            external = {"$type": "app.bsky.embed.external#external", "uri": url_md, "title": title, "description": selftext}
            embed = {"$type": "app.bsky.embed.external#main", "external": external}
            record = {"text": utils.strip_tweet(text, 300), "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
            rt = nanoatp.RichText(record["text"])
            rt.detectFacets(api)
            record.update({"facets": rt.facets}) if len(rt.facets) > 0 else None
            parent_post = api.post(record)
        except Exception as e:
            print(e)
        time.sleep(1)
    return parent_post


def upload_html_to_bluesky(api, filename, html_text, alt_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        abs_path = os.path.join(tmp_dir, filename)
        abs_path = utils.html_to_image(html_text, abs_path)
        if os.path.isfile(abs_path):
            return api.uploadImage(abs_path, alt_text)
    return None


def post_to_bluesky_trans(api: nanoatp.BskyAgent, root_post, parent_post, arxiv_id, title, authors, summary_texts, trans_texts):
    html_text = generatehtml.generate_trans_html(arxiv_id, title, authors, trans_texts, summary_texts)
    trans_text = "".join(trans_texts)
    images = []
    image = upload_html_to_bluesky(api, f"{arxiv_id}.trans.jpg", html_text, utils.strip_tweet(trans_text, 300))
    images.append(image) if image else None
    text = f"https://arxiv.org/abs/{arxiv_id}\n{trans_text}"
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": utils.strip_tweet(text, 300), "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
        rt = nanoatp.RichText(record["text"])
        rt.detectFacets(api)
        record.update({"facets": rt.facets}) if len(rt.facets) > 0 else None
        parent_post = api.post(record)
    except Exception as e:
        print(e)


def post_to_bluesky_ranking(api: nanoatp.BskyAgent, dlc, df):
    title = f"Top {len(df)} most popular arXiv papers in the last 7 days"
    date = datetime.now(timezone.utc).strftime("%d %b %Y")
    images = []
    html_text = generatehtml.generate_top_n_html(title, date, df, dlc)
    rev_df = df[::-1]
    len_df = len(df)

    def fmt(item):
        return f"[{item[0]+1}/{len_df}] https://arxiv.org/abs/{item[1][0]}"

    metadata = "\n".join(map(fmt, enumerate(zip(rev_df["arxiv_id"]))))
    image = upload_html_to_bluesky(api, "top_n.jpg", html_text, utils.strip_tweet(metadata, 300))
    images.append(image) if image else None
    text = title
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": utils.strip_tweet(text, 300), "embed": embed}
        rt = nanoatp.RichText(record["text"])
        rt.detectFacets(api)
        record.update({"facets": rt.facets}) if len(rt.facets) > 0 else None
        return api.post(record)
    except Exception as e:
        print(e)
        return None


def post_to_bluesky(api: nanoatp.BskyAgent, dlc, df, submission_df):
    df = df[::-1]  # reverse order
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    seg = pysbd.Segmenter(language="en", clean=False)
    for i, (arxiv_id, updated, title, summary, authors, primary_category, categories, score, num_comments, count) in enumerate(zip(df["arxiv_id"], df["updated"], df["title"], df["summary"], df["authors"], df["primary_category"], df["categories"], df["score"], df["num_comments"], df["count"])):
        trans_texts, trans_ts = dlc.get(arxiv_id, None)
        # only post new papers
        if not (twenty_three_hours_ago < datetime.fromisoformat(trans_ts)):
            continue
        summary_texts = seg.segment(summary.replace("\n", " ")[:2000])
        is_new = True
        parent_post = post_to_bluesky_first_page(api, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
        root_post = parent_post
        time.sleep(1)
        parent_post = post_to_bluesky_link(api, root_post, parent_post, arxiv_id, title)
        time.sleep(1)
        top_n_submissions = submission_df[submission_df["arxiv_id"].apply(lambda ids: arxiv_id in ids)].head(5)
        parent_post = post_to_bluesky_posts(api, root_post, parent_post, arxiv_id, top_n_submissions)
        post_to_bluesky_trans(api, root_post, parent_post, arxiv_id, title, authors, summary_texts, trans_texts)
        print("post_to_bluesky: ", f"[{len(df)-i}/{len(df)}]")
        time.sleep(1)
    post_to_bluesky_ranking(api, dlc, df)
