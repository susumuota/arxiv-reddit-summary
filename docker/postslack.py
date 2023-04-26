# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import re
import time
from datetime import datetime, timedelta, timezone

import dateutil.parser
import deeplcache
import pandas as pd
import pysbd
import slack_sdk
import utils


def post_to_slack_header(api: slack_sdk.WebClient, channel: str, df: pd.DataFrame):
    text = f"Top {len(df)} most popular arXiv papers in the last 30 days"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": text}}]
    return api.chat_postMessage(channel=channel, text=text, blocks=blocks)


def generate_slack_title_blocks(df: pd.DataFrame, i: int, is_new: bool, title: str, score: int, num_comments: int, count: int, primary_category: str, categories: list[str], updated: str, first_summary: str):
    new_md = ":new: " if is_new else ""
    title_md = utils.strip(title, 200)
    stats_md = f"_*{score}* Likes, {num_comments} Comments, {count} Posts_"
    categories_md = utils.avoid_auto_link(" | ".join([primary_category] + [c for c in categories if c != primary_category and re.match(r"\w+\.\w+$", c)]))
    updated_md = dateutil.parser.isoparse(updated).strftime("%d %b %Y")
    return [{"type": "section", "text": {"type": "mrkdwn", "text": f"[{len(df)-i}/{len(df)}] {new_md}*{title_md}*\n{stats_md}, {categories_md}, {updated_md}\n{first_summary}"}}]


def generate_slack_summary(dlc: deeplcache.DeepLCache, seg: pysbd.Segmenter, twenty_three_hours_ago: datetime, arxiv_id: str, summary: str):
    segs = seg.segment(summary.replace("\n", " ")[:2000])
    summary_texts: list[str] = [str(seg) for seg in segs] if type(segs) is list else [segs] if type(segs) is str else []
    first_summary = summary_texts[0][:200]  # sometimes pysbd failed to split
    translation_md = None
    is_new = False
    trans = dlc.get(arxiv_id, None)
    if trans is not None:
        trans_texts, trans_ts = trans
        first_summary = trans_texts[0][:200]  # sometimes pysbd failed to split
        is_new = True if twenty_three_hours_ago < datetime.fromisoformat(trans_ts) else False
        # assert len(summary_texts) == len(trans_texts) # this rarely happen
        if len(summary_texts) != len(trans_texts):
            print("different texts length", arxiv_id, len(summary_texts), len(trans_texts))
        translation_md = "\n\n".join(trans_texts)
        translation_md = utils.strip(translation_md, 3000)  # must be less than 3001 characters
    return is_new, first_summary, translation_md


def post_to_slack_title(api: slack_sdk.WebClient, channel: str, dlc: deeplcache.DeepLCache, df: pd.DataFrame, seg: pysbd.Segmenter, twenty_three_hours_ago: datetime, i: int, arxiv_id: str, updated: str, title: str, summary: str, primary_category: str, categories: list[str], score: int, num_comments: int, count: int):
    is_new, first_summary, translation_md = generate_slack_summary(dlc, seg, twenty_three_hours_ago, arxiv_id, summary)
    blocks = generate_slack_title_blocks(df, i, is_new, title, score, num_comments, count, primary_category, categories, updated, first_summary)
    title_md = utils.strip(title, 200)
    response = api.chat_postMessage(channel=channel, text=title_md, blocks=blocks)
    return response, translation_md


def post_to_slack_translation(api: slack_sdk.WebClient, channel: str, title: str, ts: str, translation_md: str):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": translation_md}}]
    title_md = utils.strip(title, 200)
    return api.chat_postMessage(channel=channel, text=title_md, blocks=blocks, thread_ts=ts)


def post_to_slack_authors(api: slack_sdk.WebClient, channel: str, title: str, ts: str, authors: list[str], comment: str, arxiv_id: str):
    authors_md = utils.strip(", ".join(authors), 1000)
    comment_md = f"\n\n*Comments*: {utils.strip(comment, 1000)}\n\n" if comment else ""
    abs_md = f"<https://arxiv.org/abs/{arxiv_id}|abs>"
    pdf_md = f"<https://arxiv.org/pdf/{arxiv_id}.pdf|pdf>"
    twitter_md = f"<https://twitter.com/search?q=arxiv.org%2Fabs%2F{arxiv_id}%20OR%20arxiv.org%2Fpdf%2F{arxiv_id}.pdf|Twitter>"
    reddit_md = f"<https://www.reddit.com/search/?q=%22{arxiv_id}%22&sort=top|Reddit>"
    hackernews_md = f"<https://hn.algolia.com/?query=%22{arxiv_id}%22&type=all|HackerNews>"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*Links*: {abs_md}, {pdf_md}, {twitter_md}, {reddit_md}, {hackernews_md}\n\n*Authors*: {authors_md}{comment_md}"}}]
    title_md = utils.strip(title, 200)
    return api.chat_postMessage(channel=channel, text=title_md, blocks=blocks, thread_ts=ts)


def post_to_slack_documents(api: slack_sdk.WebClient, channel: str, ts: str, df: pd.DataFrame):
    for i, (id, score, num_comments, created_at) in enumerate(zip(df["id"], df["score"], df["num_comments"], df["created_at"])):
        blocks = []
        stats_md = f"_*{score}* Likes, {num_comments} Comments_"
        created_at_md = datetime.fromtimestamp(created_at).strftime("%d %b %Y")
        url_md = f"<{id}|{created_at_md}>"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"({i+1}/{len(df)}) {stats_md}, {url_md}\n"}}]
        api.chat_postMessage(channel=channel, text=url_md, thread_ts=ts, blocks=blocks)
        time.sleep(1)


def post_to_slack(api: slack_sdk.WebClient, channel: str, dlc: deeplcache.DeepLCache, df: pd.DataFrame, document_df: pd.DataFrame):
    df = df[::-1]  # reverse order
    post_to_slack_header(api, channel, df)
    time.sleep(1)
    seg = pysbd.Segmenter(language="en", clean=False)
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    for i, (arxiv_id, updated, title, summary, authors, comment, primary_category, categories, score, num_comments, count) in enumerate(zip(df["arxiv_id"], df["updated"], df["title"], df["summary"], df["authors"], df["comment"], df["primary_category"], df["categories"], df["score"], df["num_comments"], df["count"])):
        response, translation_md = post_to_slack_title(api, channel, dlc, df, seg, twenty_three_hours_ago, i, arxiv_id, updated, title, summary, primary_category, categories, score, num_comments, count)
        time.sleep(1)
        ts = response["ts"]
        if not ts:
            continue
        if translation_md:
            post_to_slack_translation(api, channel, title, ts, translation_md)
            time.sleep(1)
        post_to_slack_authors(api, channel, title, ts, authors, comment, arxiv_id)
        time.sleep(1)
        top_n_documents = document_df[document_df["arxiv_id"].apply(lambda ids: arxiv_id in ids)].head(5)
        post_to_slack_documents(api, channel, ts, top_n_documents)
        print("post_to_slack: ", f"[{len(df)-i}/{len(df)}]")
