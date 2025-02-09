# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
#
# SPDX-License-Identifier: MIT

# Those environment variables are required to use PRAW.
# export praw_client_id="reddit client id"
# export praw_client_secret="reddit client secret"
# export praw_user_agent="reddit user agent"

import os
import re
import time
from datetime import datetime, timedelta, timezone

import arxiv
import deepl
import deeplcache
import nanoatp
import pandas as pd
import postbluesky
import postslack
import posttwitter
import praw
import pysbd
import requests
import slack_sdk
import tweepy
from bs4 import BeautifulSoup, Tag
from google.cloud import storage


ARXIV_URL_PATTERN = re.compile(r"https?://arxiv\.org/(abs|pdf)/([0-9]{4}\.[0-9]{4,6})(v[0-9]+)?(\.pdf)?")


def parse_arxiv_ids(text: str) -> list[str]:
    text = text.replace("\\", "")  # TODO: some text includes 2 backslashes in urls
    return list(set([m[1] for m in re.findall(ARXIV_URL_PATTERN, text)]))


def flatten(lists: list[list]):
    return [item for sublist in lists for item in sublist]


def submission_to_dict(submission: praw.reddit.Submission):
    """https://praw.readthedocs.io/en/stable/code_overview/models/submission.html"""
    arxiv_ids = parse_arxiv_ids(submission.selftext)
    score = int(submission.score / len(arxiv_ids) if len(arxiv_ids) > 0 else submission.score)
    return {
        "id": f"https://redd.it/{submission.id}",
        "score": score,
        "num_comments": submission.num_comments,
        "created_at": submission.created_utc,
        "arxiv_id": arxiv_ids,
        "title": submission.title,
        "description": submission.selftext,
    }


def search_reddit(query: str, sort="relevance", syntax="lucene", time_filter="all", limit: int | None = None):
    """https://praw.readthedocs.io/en/latest/code_overview/models/subreddit.html#praw.models.Subreddit.search"""
    rs = list(praw.Reddit().subreddit("all").search(query=query, sort=sort, syntax=syntax, time_filter=time_filter, limit=limit))
    return pd.json_normalize([submission_to_dict(r) for r in rs])


def hit_to_dict(hit: dict):
    """https://hn.algolia.com/api"""
    arxiv_ids = parse_arxiv_ids(hit["url"])
    score = int(hit["points"] / len(arxiv_ids) if len(arxiv_ids) > 0 else hit["points"])
    return {
        "id": f"https://news.ycombinator.com/item?id={hit['objectID']}",
        "score": score,
        "num_comments": hit["num_comments"],
        "created_at": hit["created_at_i"],
        "arxiv_id": arxiv_ids,
        "title": hit["title"],
        "description": hit["url"],
    }


def search_hackernews(query: str, attribute="", days=0, limit: int | None = None):
    """https://hn.algolia.com/api"""
    params = {"query": query}
    params.update({"restrictSearchableAttributes": attribute}) if attribute else None
    if days > 0:
        days_ago = int((datetime.now() - timedelta(days=days)).timestamp())
        params.update({"numericFilters": f"created_at_i>{days_ago}"})
    params.update({"hitsPerPage": str(limit)}) if limit else None
    response = requests.get("https://hn.algolia.com/api/v1/search", params=params)
    json = response.json()
    return pd.json_normalize([hit_to_dict(hit) for hit in json["hits"]])


def article_to_dict(article: Tag, created_at: float):
    """https://huggingface.co/papers"""
    h3_a = article.select_one("h3 > a")
    arxiv_id = "" if h3_a is None else str(h3_a["href"].split("/")[-1])  # TODO: check if arxiv_id is valid
    score_div = article.select_one("div[class^=leading]")  # TODO: better selector
    score = 0 if score_div is None else int(score_div.text) if re.match(r"^\d+$", score_div.text) else 0
    num_comments_a = article.select_one("a[href$='#community']")
    num_comments = 0 if num_comments_a is None else int(num_comments_a.text)
    return {
        "id": f"https://huggingface.co/papers/{arxiv_id}",
        "score": score,
        "num_comments": num_comments,
        "created_at": created_at,
        "arxiv_id": [arxiv_id],
        "title": h3_a.text,
        "description": f"https://arxiv.org/abs/{arxiv_id}",
    }


def scrape_huggingface(timestamp: float, wait: int = 1):
    """https://huggingface.co/papers"""
    date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    response = requests.get(f"https://huggingface.co/papers?date={date}")
    soup = BeautifulSoup(response.text, "html.parser")
    articles = soup.select("article")
    result = [article_to_dict(article, timestamp) for article in articles]
    print(f"scraped {len(result)} articles from {date}")
    time.sleep(wait)
    return result


def search_huggingface(days: int = 30):
    """https://huggingface.co/papers"""
    now = datetime.now()
    timestamps = [(now - timedelta(days=d)).timestamp() for d in range(days)]
    df = pd.json_normalize(flatten([scrape_huggingface(ts) for ts in timestamps]))
    # sometimes there are no articles for a given date.
    # in that case, HF returns the article from the previous day.
    # so we need to drop duplicates.
    return df.drop_duplicates(subset=["id"], keep="last").reset_index(drop=True)


def get_arxiv_stats(document_df: pd.DataFrame):
    return document_df.explode("arxiv_id").groupby("arxiv_id").agg(score=("score", "sum"), num_comments=("num_comments", "sum"), count=("id", "count"), document_id=("id", pd.Series.to_list)).sort_values(by=["score", "num_comments", "count"], ascending=False).reset_index()


def arxiv_result_to_dict(r: arxiv.Result):
    m = ARXIV_URL_PATTERN.match(r.entry_id)
    arxiv_id = m.group(2) if m else None
    assert arxiv_id is not None
    arxiv_id_v = m.group(2) + m.group(3) if m else None
    assert arxiv_id_v is not None
    return {
        "arxiv_id": arxiv_id,
        "arxiv_id_v": arxiv_id_v,
        "entry_id": r.entry_id,
        "updated": str(r.updated),  # TODO
        "published": str(r.published),  # TODO
        "title": r.title,
        "authors": [str(a) for a in r.authors],
        "summary": r.summary,
        "comment": r.comment,
        "journal_ref": r.journal_ref,
        "doi": r.doi,
        "primary_category": r.primary_category,
        "categories": [str(c) for c in r.categories],
        "links": [str(link) for link in r.links],
        "pdf_url": r.pdf_url,
    }


def get_arxiv_contents(id_list: list[str], chunk_size=100):
    rs: list[arxiv.Result] = []
    cdr = id_list
    for i in range(1 + len(id_list) // chunk_size):
        car = cdr[:chunk_size]
        cdr = cdr[chunk_size:]
        if len(car) > 0:
            try:
                search = arxiv.Search(id_list=car, max_results=len(car))
                r = list(search.results())
                rs.extend(r)
                print("search_arxiv_contents: ", i, len(r), len(rs))
            except Exception as e:
                print(e)
    return pd.json_normalize([arxiv_result_to_dict(r) for r in rs])


def filter_df(df: pd.DataFrame, top_n=10, days=365):
    days_ago = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")  # noqa: F841
    return df.query("published > @days_ago").head(top_n).reset_index(drop=True)


def summarize(query, time_filter="month", days=30, limit=300):
    print("search_reddit...")
    reddit_document_df = search_reddit(f"selftext:{query}", sort="top", time_filter=time_filter, limit=limit)
    print("search_reddit...done: ", len(reddit_document_df))
    print("search_hackernews...")
    hackernews_document_df = search_hackernews(query, attribute="url", days=days, limit=limit)
    print("search_hackernews...done: ", len(hackernews_document_df))
    print("search_huggingface...")
    search_huggingface_df = search_huggingface(days=days)
    print("search_huggingface...done: ", len(search_huggingface_df))
    document_df = pd.concat([reddit_document_df, hackernews_document_df, search_huggingface_df], ignore_index=True).sort_values(by=["score", "num_comments"], ascending=False).reset_index(drop=True)
    print("document_df: ", len(document_df))
    stats_df = get_arxiv_stats(document_df)
    print("stats_df: ", len(stats_df))
    contents_df = get_arxiv_contents(stats_df["arxiv_id"].tolist(), chunk_size=100)
    print("contents_df: ", len(contents_df))
    paper_df = pd.merge(stats_df, contents_df, on="arxiv_id")
    print("paper_df: ", len(paper_df))
    return paper_df, document_df


def translate_arxiv(dlc: deeplcache.DeepLCache, df: pd.DataFrame, target_lang: str):
    seg = pysbd.Segmenter(language="en", clean=False)
    print("translate_arxiv: before: ", len(dlc.cache))
    print(dlc.translator.get_usage())
    for arxiv_id, summary in zip(df["arxiv_id"], df["summary"]):
        summary_texts = seg.segment(summary.replace("\n", " ")[:2000])
        trans_texts, trans_ts = dlc.translate_text(summary_texts, target_lang, arxiv_id)
        print("translate_arxiv: ", arxiv_id, sum([len(s) for s in summary_texts]), sum([len(t) for t in trans_texts]), trans_ts)
    print("translate_arxiv: after: ", len(dlc.cache))
    print(dlc.translator.get_usage())
    return dlc


def main():
    # settings
    query = "arxiv.org"
    summarize_time_filter = "month"  # or "week"
    summarize_days = 30  # should be 30 if "month"
    summarize_limit = 500
    filter_days = 30
    deepl_target_lang = "JA"
    deepl_expire_days = 90
    notify_top_n = int(os.getenv("NOTIFY_TOP_N", 10))

    # prepare apis
    gcs_bucket = storage.Client().bucket(os.getenv("GCS_BUCKET_NAME"))
    deepl_api = deepl.Translator(os.getenv("DEEPL_AUTH_KEY"))  # type: ignore
    slack_api = slack_sdk.WebClient(os.getenv("SLACK_BOT_TOKEN"))
    slack_channel = os.getenv("SLACK_CHANNEL")
    tweepy_api_v2 = tweepy.Client(bearer_token=os.getenv("TWITTER_BEARER_TOKEN"), consumer_key=os.getenv("TWITTER_API_KEY"), consumer_secret=os.getenv("TWITTER_API_KEY_SECRET"), access_token=os.getenv("TWITTER_ACCESS_TOKEN"), access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET"), wait_on_rate_limit=True)
    # because media_upload is only available on api v1.
    tweepy_api_v1 = tweepy.API(tweepy.OAuth1UserHandler(consumer_key=os.getenv("TWITTER_API_KEY"), consumer_secret=os.getenv("TWITTER_API_KEY_SECRET"), access_token=os.getenv("TWITTER_ACCESS_TOKEN"), access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")), wait_on_rate_limit=True)
    bluesky_api = nanoatp.BskyAgent()
    bluesky_api.login(os.getenv("ATP_IDENTIFIER"), os.getenv("ATP_PASSWORD"))  # type: ignore

    # search reddit and measure popularity
    paper_df, document_df = summarize(query, time_filter=summarize_time_filter, days=summarize_days, limit=summarize_limit)

    # filter by days
    filtered_df = filter_df(paper_df, top_n=notify_top_n, days=filter_days)
    print("filtered_df: ", len(filtered_df))

    # translate summary text
    dlc = deeplcache.DeepLCache(deepl_api)
    try:
        dlc.load_from_gcs(gcs_bucket, "deepl_cache.json.gz")
    except Exception as e:
        print(e)
    dlc = translate_arxiv(dlc, filtered_df, deepl_target_lang)
    dlc.clear_cache(expire_timedelta=timedelta(days=deepl_expire_days))
    dlc.save_to_gcs(gcs_bucket, "deepl_cache.json.gz")

    # post
    postslack.post_to_slack(slack_api, slack_channel, dlc, filtered_df, document_df)  # type: ignore

    postbluesky.post_to_bluesky(bluesky_api, dlc, filtered_df, document_df)

    posttwitter.post_to_twitter(tweepy_api_v1, tweepy_api_v2, dlc, filtered_df, document_df)


if __name__ == "__main__":
    main()
