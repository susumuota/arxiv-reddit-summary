# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import deeplcache
import generatehtml
import pandas as pd
import pysbd
import tweepy
import utils


def upload_first_page_to_twitter(api_v1: tweepy.API, arxiv_id: str):
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_filename = utils.download_arxiv_pdf(arxiv_id, tmp_dir)
        first_page_filename = utils.pdf_to_png(pdf_filename)
        if os.path.isfile(first_page_filename):
            media = api_v1.media_upload(first_page_filename)
            return media.media_id if media else None
    return None


def post_to_twitter_first_page(api_v1: tweepy.API, api_v2: tweepy.Client, df: pd.DataFrame, i: int, is_new: bool, arxiv_id: str, updated: str, title: str, summary_texts: list[str], authors: list[str], score: int, num_comments: int, count: int, primary_category: str, categories: list[str]) -> str:
    text, summary_text = utils.generate_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
    media_ids = []
    first_page_media_id = upload_first_page_to_twitter(api_v1, arxiv_id)
    if first_page_media_id:
        api_v1.create_media_metadata(first_page_media_id, utils.strip_tweet(summary_text, 1000))
        media_ids.append(first_page_media_id)
    prev_tweet_id: str = ""
    try:
        response = api_v2.create_tweet(text=utils.strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None)
        prev_tweet_id = response.data["id"] if type(response) is tweepy.Response and not response.errors else ""
    except Exception as e:
        print(e)
    return prev_tweet_id


def post_to_twitter_link(api_v2: tweepy.Client, prev_tweet_id: str, arxiv_id: str) -> str:
    uri = f"https://twitter.com/search?q=arxiv.org%2Fabs%2F{arxiv_id}"
    text = f"Twitter Search: {uri}"
    try:
        response = api_v2.create_tweet(text=utils.strip_tweet(text, 280), user_auth=True, in_reply_to_tweet_id=prev_tweet_id)
        prev_tweet_id = response.data["id"] if type(response) is tweepy.Response and not response.errors else ""
    except Exception as e:
        print(e)
    return prev_tweet_id


def post_to_twitter_tweets(api_v2: tweepy.Client, prev_tweet_id: str, df: pd.DataFrame) -> str:
    for i, (id, score, num_comments, created_at) in enumerate(zip(df["id"], df["score"], df["num_comments"], df["created_at"])):
        stats_md = f"{score} Likes, {num_comments} Comments"
        created_at_md = datetime.fromtimestamp(created_at).strftime("%d %b %Y")
        text = f"({i+1}/{len(df)}) {stats_md}, {created_at_md}\n{id}\n"
        try:
            response = api_v2.create_tweet(text=utils.strip_tweet(text, 280), user_auth=True, in_reply_to_tweet_id=prev_tweet_id)
            prev_tweet_id = response.data["id"] if type(response) is tweepy.Response and not response.errors else ""
        except Exception as e:
            print(e)
        time.sleep(1)
    return prev_tweet_id


def upload_html_to_twitter(api_v1: tweepy.API, filename: str, html_text: str):
    with tempfile.TemporaryDirectory() as tmp_dir:
        abs_path = os.path.join(tmp_dir, filename)
        abs_path = utils.html_to_image(html_text, abs_path)
        if os.path.isfile(abs_path):
            media = api_v1.media_upload(abs_path)
            return media.media_id if media else None
    return None


def post_to_twitter_ranking(api_v1: tweepy.API, api_v2: tweepy.Client, dlc: deeplcache.DeepLCache, df: pd.DataFrame):
    title = f"Top {len(df)} most popular arXiv papers in the last 30 days"
    date = datetime.now(timezone.utc).strftime("%d %b %Y")
    media_ids = []
    html_text = generatehtml.generate_top_n_html(title, date, df, dlc)
    top_n_media_id = upload_html_to_twitter(api_v1, "top_n.jpg", html_text)
    if top_n_media_id:
        rev_df = df[::-1]
        metadata = "\n".join(map(lambda item: f"[{item[0]+1}/{len(df)}] https://arxiv.org/abs/{item[1][0]}", enumerate(zip(rev_df["arxiv_id"]))))
        api_v1.create_media_metadata(top_n_media_id, utils.strip_tweet(metadata, 1000))
        media_ids.append(top_n_media_id)
    text = title
    try:
        api_v2.create_tweet(text=utils.strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None)
    except Exception as e:
        print(e)


def post_to_twitter_trans(api_v1: tweepy.API, api_v2: tweepy.Client, prev_tweet_id: str, arxiv_id: str, title: str, authors: list[str], summary_texts: list[str], trans_texts: list[str]):
    html_text = generatehtml.generate_trans_html(arxiv_id, title, authors, trans_texts, summary_texts)
    media_ids = []
    translation_media_id = upload_html_to_twitter(api_v1, f"{arxiv_id}.trans.jpg", html_text)
    trans_text = "".join(trans_texts)
    if translation_media_id:
        api_v1.create_media_metadata(translation_media_id, utils.strip_tweet(trans_text, 1000))
        media_ids.append(translation_media_id)
    text = f"https://arxiv.org/abs/{arxiv_id}\n{trans_text}"
    try:
        api_v2.create_tweet(text=utils.strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None, in_reply_to_tweet_id=prev_tweet_id)
    except Exception as e:
        print(e)


def post_to_twitter(api_v1: tweepy.API, api_v2: tweepy.Client, dlc: deeplcache.DeepLCache, df: pd.DataFrame, document_df: pd.DataFrame):
    df = df[::-1]  # reverse order
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    seg = pysbd.Segmenter(language="en", clean=False)
    for i, (arxiv_id, updated, title, summary, authors, comment, primary_category, categories, score, num_comments, count) in enumerate(zip(df["arxiv_id"], df["updated"], df["title"], df["summary"], df["authors"], df["comment"], df["primary_category"], df["categories"], df["score"], df["num_comments"], df["count"])):
        trans = dlc.get(arxiv_id, None)
        if trans is None:
            continue
        trans_texts, trans_ts = trans
        segs = seg.segment(summary.replace("\n", " ")[:2000])
        summary_texts: list[str] = [str(seg) for seg in segs] if type(segs) is list else [segs] if type(segs) is str else []
        # only post new papers
        if not (twenty_three_hours_ago < datetime.fromisoformat(trans_ts)):
            continue
        is_new = True
        prev_tweet_id = post_to_twitter_first_page(api_v1, api_v2, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
        time.sleep(1)
        if not prev_tweet_id:
            continue
        prev_tweet_id = post_to_twitter_link(api_v2, prev_tweet_id, arxiv_id)
        time.sleep(1)
        if not prev_tweet_id:
            continue
        top_n_documents = document_df[document_df["arxiv_id"].apply(lambda ids: arxiv_id in ids)].head(5)
        prev_tweet_id = post_to_twitter_tweets(api_v2, prev_tweet_id, top_n_documents)
        post_to_twitter_trans(api_v1, api_v2, prev_tweet_id, arxiv_id, title, authors, summary_texts, trans_texts)
        print("post_to_twitter: ", f"[{len(df)-i}/{len(df)}]")
        time.sleep(1)
    post_to_twitter_ranking(api_v1, api_v2, dlc, df)
