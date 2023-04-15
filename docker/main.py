# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
#
# SPDX-License-Identifier: MIT

# Those environment variables are required to use PRAW.
# export praw_client_id="reddit client id"
# export praw_client_secret="reddit client secret"
# export praw_user_agent="reddit user agent"

import os
import re
import subprocess
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from shlex import quote

import arxiv
import dateutil.parser
import deepl
import deeplcache
import imgkit
import nanoatp
import pandas as pd
import praw
import pysbd
import tweepy
from generatehtml import generate_top_n_html, generate_trans_html
from google.cloud import storage
from slack_sdk import WebClient


ARXIV_URL_PATTERN = re.compile(r'https?://arxiv\.org/(abs|pdf)/([0-9]{4}\.[0-9]{4,6})(v[0-9]+)?(\.pdf)?')


def parse_arxiv_ids(text: str) -> list[str]:
    text = text.replace('\\', '')  # TODO: some text includes 2 backslashes in urls
    return list(set([m[1] for m in re.findall(ARXIV_URL_PATTERN, text)]))


def submission_to_dict(submission: praw.reddit.Submission):
    return {
        'id': submission.id,
        'score': submission.score,
        'num_comments': submission.num_comments,
        'created_utc': submission.created_utc,
        'arxiv_id': parse_arxiv_ids(submission.selftext),
        'title': submission.title,
    }


def search_reddit(query: str, sort='relevance', syntax='lucene', time_filter='all', limit: int | None = None):
    # https://praw.readthedocs.io/en/latest/code_overview/models/subreddit.html#praw.models.Subreddit.search
    rs = list(praw.Reddit().subreddit('all').search(query=query, sort=sort, syntax=syntax, time_filter=time_filter, limit=limit))
    return pd.json_normalize([submission_to_dict(r) for r in rs])


def get_arxiv_stats(submission_df: pd.DataFrame):
    return submission_df.explode('arxiv_id').groupby('arxiv_id').agg(score=('score', 'sum'), num_comments=('num_comments', 'sum'), count=('id', 'count'), submission_id=('id', pd.Series.to_list)).sort_values(by=['score', 'num_comments', 'count'], ascending=False).reset_index()


def arxiv_result_to_dict(r: arxiv.Result):
    m = ARXIV_URL_PATTERN.match(r.entry_id)
    arxiv_id = m.group(2) if m else None
    assert arxiv_id is not None
    arxiv_id_v = m.group(2) + m.group(3) if m else None
    assert arxiv_id_v is not None
    return {
        'arxiv_id': arxiv_id,
        'arxiv_id_v': arxiv_id_v,
        'entry_id': r.entry_id,
        'updated': str(r.updated), # TODO
        'published': str(r.published), # TODO
        'title': r.title,
        'authors': [str(a) for a in r.authors],
        'summary': r.summary,
        'comment': r.comment,
        'journal_ref': r.journal_ref,
        'doi': r.doi,
        'primary_category': r.primary_category,
        'categories': [str(c) for c in r.categories],
        'links': [str(l) for l in r.links],
        'pdf_url': r.pdf_url
    }


def get_arxiv_contents(id_list: list[str], chunk_size=100):
    rs: list[arxiv.Result] = []
    cdr = id_list
    for i in range(1+len(id_list)//chunk_size):
        car = cdr[:chunk_size]
        cdr = cdr[chunk_size:]
        if len(car) > 0:
            try:
                search = arxiv.Search(id_list=car, max_results=len(car))
                r = list(search.results())
                rs.extend(r)
                print('search_arxiv_contents: ', i, len(r), len(rs))
            except Exception as e:
                print(e)
    return pd.json_normalize([arxiv_result_to_dict(r) for r in rs])


def filter_df(df: pd.DataFrame, top_n: int = 10, days: int = 365):
    days_ago = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
    return df.query(f'published > @days_ago').head(top_n).reset_index(drop=True)


def summarize(query: str):
    submission_df = search_reddit(query, sort='top', time_filter='week', limit=100)
    stats_df = get_arxiv_stats(submission_df)
    contents_df = get_arxiv_contents(stats_df['arxiv_id'].tolist(), chunk_size=100)
    paper_df = pd.merge(stats_df, contents_df, on='arxiv_id')
    return paper_df, submission_df


def get_char_width(c: str):
    return 2 if unicodedata.east_asian_width(c) in 'FWA' else 1


def len_tweet(text: str):
    return sum(map(get_char_width, text))


def strip_tweet(text: str, max_length=280, dots='...'):
    length = max_length - (len(dots) if dots else 0)
    buf = []
    count = 0
    for c in text:
        width = get_char_width(c)
        if count + width > length:
            return ''.join(buf) + (dots if dots else '')
        buf.append(c)
        count += width
    return text


def translate_arxiv(dlc: deeplcache.DeepLCache, df, target_lang):
    seg = pysbd.Segmenter(language='en', clean=False)
    print('translate_arxiv: before: ', len(dlc.cache))
    print(dlc.translator.get_usage())
    for arxiv_id, summary in zip(df['arxiv_id'], df['summary']):
        summary_texts = seg.segment(summary.replace('\n', ' ')[:2000])
        trans_texts, trans_ts = dlc.translate_text(summary_texts, target_lang, arxiv_id)
        print('translate_arxiv: ', arxiv_id, sum([len(s) for s in summary_texts]), sum([len(t) for t in trans_texts]), trans_ts)
    print('translate_arxiv: after: ', len(dlc.cache))
    print(dlc.translator.get_usage())
    return dlc


def post_to_slack_header(api, channel, df):
    text = f'Top {len(df)} most popular arXiv papers in the last 7 days'
    blocks = [{'type': 'header', 'text': {'type': 'plain_text', 'text': text}}]
    api.chat_postMessage(channel=channel, text=text, blocks=blocks)


def avoid_auto_link(text):
    """replace period to one dot leader to avoid auto link.
    https://shkspr.mobi/blog/2015/01/how-to-stop-twitter-auto-linking-urls/"""
    return text.replace('.', '․')


def strip(s, l):
    return s[:l-3] + '...' if len(s) > l else s


def generate_slack_title_blocks(df, i, is_new, title, score, num_comments, count, primary_category, categories, updated, first_summary):
    new_md = ':new: ' if is_new else ''
    title_md = strip(title, 200)
    stats_md = f'_*{score}* Upvotes, {num_comments} Comments, {count} Posts_'
    categories_md = avoid_auto_link(' | '.join([c for c in [primary_category] + [c for c in categories if c != primary_category and re.match(r'\w+\.\w+$', c)]]))
    updated_md = dateutil.parser.isoparse(updated).strftime('%d %b %Y')
    return [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'[{len(df)-i}/{len(df)}] {new_md}*{title_md}*\n{stats_md}, {categories_md}, {updated_md}\n{first_summary}'}}]


def generate_slack_summary(dlc, seg, twenty_three_hours_ago, arxiv_id, summary):
    summary_texts = seg.segment(summary.replace('\n', ' ')[:2000])
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
            print('different texts length', arxiv_id, len(summary_texts), len(trans_texts))
        translation_md = '\n\n'.join(trans_texts)
        translation_md = strip(translation_md, 3000)  # must be less than 3001 characters
    return is_new, first_summary, translation_md


def post_to_slack_title(api, channel, dlc, df, seg, twenty_three_hours_ago, i, arxiv_id, updated, title, summary, primary_category, categories, score, num_comments, count):
    is_new, first_summary, translation_md = generate_slack_summary(dlc, seg, twenty_three_hours_ago, arxiv_id, summary)
    blocks = generate_slack_title_blocks(df, i, is_new, title, score, num_comments, count, primary_category, categories, updated, first_summary)
    title_md = strip(title, 200)
    response = api.chat_postMessage(channel=channel, text=title_md, blocks=blocks)
    return response, translation_md


def post_to_slack_translation(api, channel, title, ts, translation_md):
    if translation_md is not None:
        blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': translation_md}}]
        title_md = strip(title, 200)
        api.chat_postMessage(channel=channel, text=title_md, blocks=blocks, thread_ts=ts)


def post_to_slack_authors(api, channel, title, ts, authors, comment, arxiv_id):
    authors_md = strip(', '.join(authors), 1000)
    comment_md = f'\n\n*Comments*: {strip(comment, 1000)}\n\n' if comment else ''
    abs_md = f'<https://arxiv.org/abs/{arxiv_id}|abs>'
    pdf_md = f'<https://arxiv.org/pdf/{arxiv_id}.pdf|pdf>'
    tweets_md = f'<https://twitter.com/search?q=arxiv.org%2Fabs%2F{arxiv_id}%20OR%20arxiv.org%2Fpdf%2F{arxiv_id}.pdf|Tweets>'
    blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Links*: {abs_md}, {pdf_md}, {tweets_md}\n\n*Authors*: {authors_md}{comment_md}'}}]
    title_md = strip(title, 200)
    api.chat_postMessage(channel=channel, text=title_md, blocks=blocks, thread_ts=ts)


def post_to_slack(api, channel, dlc, df, submission_df):
    df = df[::-1]  # reverse order
    post_to_slack_header(api, channel, df)
    time.sleep(1)
    seg = pysbd.Segmenter(language='en', clean=False)
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    for i, (arxiv_id, updated, title, summary, authors, comment, primary_category, categories, score, num_comments, count) in enumerate(zip(df['arxiv_id'], df['updated'], df['title'], df['summary'], df['authors'], df['comment'], df['primary_category'], df['categories'], df['score'], df['num_comments'], df['count'])):
        response, translation_md = post_to_slack_title(api, channel, dlc, df, seg, twenty_three_hours_ago, i, arxiv_id, updated, title, summary, primary_category, categories, score, num_comments, count)
        time.sleep(1)
        ts = response['ts']
        post_to_slack_translation(api, channel, title, ts, translation_md)
        time.sleep(1)
        post_to_slack_authors(api, channel, title, ts, authors, comment, arxiv_id)
        time.sleep(1)
        top_n_submissions = submission_df[submission_df['arxiv_id'].apply(lambda ids: arxiv_id in ids)].head(5)
        post_to_slack_submissions(api, channel, ts, top_n_submissions)
        print('post_to_slack: ', f'[{len(df)-i}/{len(df)}]')


def post_to_slack_submissions(api, channel, ts, df):
    for i, (id, score, num_comments, created_utc) in enumerate(zip(df['id'], df['score'], df['num_comments'], df['created_utc'])):
        blocks = []
        stats_md = f'_*{score}* Upvotes, {num_comments} Comments_'
        created_at_md = datetime.fromtimestamp(created_utc).strftime('%d %b %Y')
        url_md = f'<http://reddit.com/{id}|{created_at_md}>'
        blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'({i+1}/{len(df)}) {stats_md}, {url_md}\n'}}]
        api.chat_postMessage(channel=channel, text=url_md, thread_ts=ts, blocks=blocks)
        time.sleep(1)


def download_arxiv_pdf(arxiv_id, tmp_dir):
    dir = quote(tmp_dir)
    output = quote(f'{arxiv_id}.pdf')
    url = quote(f'https://arxiv.org/pdf/{arxiv_id}.pdf')
    result = subprocess.run(f'aria2c -q -x5 -k1M -d {dir} -o {output} {url}', shell=True)
    assert result.returncode == 0  # TODO
    return os.path.join(tmp_dir, f'{arxiv_id}.pdf')


def pdf_to_png(pdf_filename):
    filename = quote(pdf_filename)
    result = subprocess.run(f'pdftoppm -q -png -singlefile -scale-to-x 1200 -scale-to-y -1 {filename} {filename}', shell=True)
    assert result.returncode == 0  # TODO
    return f'{pdf_filename}.png'


def html_to_image(html, image_filename):
    result = imgkit.from_string(html, image_filename, options={'width': 1200, 'quiet': ''})
    assert result is True  # TODO
    return image_filename


def upload_first_page_to_twitter(api_v1, arxiv_id):
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_filename = download_arxiv_pdf(arxiv_id, tmp_dir)
        first_page_filename = pdf_to_png(pdf_filename)
        if os.path.isfile(first_page_filename):
            media = api_v1.media_upload(first_page_filename)
            return media.media_id
    return None


def generate_twitter_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories):
    summary_text = ' '.join(summary_texts)
    new_md = '🆕' if is_new else ''
    authors_md = ', '.join(authors)
    categories_md = avoid_auto_link(' | '.join([c for c in [primary_category] + [c for c in categories if c != primary_category and re.match(r'\w+\.\w+$', c)]]))
    stats_md = f'{score} Upvotes, {num_comments} Comments, {count} Posts'
    updated_md = dateutil.parser.isoparse(updated).strftime('%d %b %Y')
    title_md = title
    abs_md = f'https://arxiv.org/abs/{arxiv_id}'
    text = f'[{len(df)-i}/{len(df)}] {stats_md}\n{abs_md} {categories_md}, {updated_md}\n\n{new_md}{title_md}\n\n{authors_md}'
    return text, summary_text


def post_to_twitter_first_page(api_v1, api_v2, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories):
    text, summary_text = generate_twitter_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
    media_ids = []
    first_page_media_id = upload_first_page_to_twitter(api_v1, arxiv_id)
    if first_page_media_id:
        api_v1.create_media_metadata(first_page_media_id, strip_tweet(summary_text, 1000))
        media_ids.append(first_page_media_id)
    prev_tweet_id = None
    try:
        response = api_v2.create_tweet(text=strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None)
        prev_tweet_id = response.data['id']
    except Exception as e:
        print(e)
    return prev_tweet_id


def post_to_twitter_tweets(api_v2, prev_tweet_id, arxiv_id, df):
    for i, (id, score, num_comments, created_utc) in enumerate(zip(df['id'], df['score'], df['num_comments'], df['created_utc'])):
        stats_md = f'{score} Upvotes, {num_comments} Comments'
        created_at_md = datetime.fromtimestamp(created_utc).strftime('%d %b %Y')
        url_md = f'http://reddit.com/{id}'
        text = f'({i+1}/{len(df)}) {stats_md}, {created_at_md}\n{url_md}\n'
        try:
            response = api_v2.create_tweet(text=strip_tweet(text, 280), user_auth=True, in_reply_to_tweet_id=prev_tweet_id)
            prev_tweet_id = response.data['id']
        except Exception as e:
            print(e)
        time.sleep(1)
    return prev_tweet_id


def upload_html_to_twitter(api_v1, filename, html_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        abs_path = os.path.join(tmp_dir, filename)
        abs_path = html_to_image(html_text, abs_path)
        if os.path.isfile(abs_path):
            media = api_v1.media_upload(abs_path)
            return media.media_id
    return None


def post_to_twitter_ranking(api_v1, api_v2, dlc, df):
    title = f'Top {len(df)} most popular arXiv papers in the last 7 days'
    date = datetime.now(timezone.utc).strftime('%d %b %Y')
    media_ids = []
    html_text = generate_top_n_html(title, date, df, dlc)
    top_n_media_id = upload_html_to_twitter(api_v1, 'top_n.jpg', html_text)
    if top_n_media_id:
        rev_df = df[::-1]
        metadata = '\n'.join(map(lambda item: f'[{item[0]+1}/{len(df)}] https://arxiv.org/abs/{item[1][0]}', enumerate(zip(rev_df['arxiv_id']))))
        api_v1.create_media_metadata(top_n_media_id, strip_tweet(metadata, 1000))
        media_ids.append(top_n_media_id)
    text = title
    try:
        api_v2.create_tweet(text=strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None)
    except Exception as e:
        print(e)


def post_to_twitter_trans(api_v1, api_v2, prev_tweet_id, arxiv_id, title, authors, summary_texts, trans_texts):
    html_text = generate_trans_html(arxiv_id, title, authors, trans_texts, summary_texts)
    media_ids = []
    translation_media_id = upload_html_to_twitter(api_v1, f'{arxiv_id}.trans.jpg', html_text)
    trans_text = ''.join(trans_texts)
    if translation_media_id:
        api_v1.create_media_metadata(translation_media_id, strip_tweet(trans_text, 1000))
        media_ids.append(translation_media_id)
    text = f'https://arxiv.org/abs/{arxiv_id}\n{trans_text}'
    try:
        api_v2.create_tweet(text=strip_tweet(text, 280), user_auth=True, media_ids=media_ids if len(media_ids) > 0 else None, in_reply_to_tweet_id=prev_tweet_id)
    except Exception as e:
        print(e)


def post_to_twitter(api_v1, api_v2, dlc, df, submission_df):
    df = df[::-1]  # reverse order
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    seg = pysbd.Segmenter(language='en', clean=False)
    for i, (arxiv_id, updated, title, summary, authors, comment, primary_category, categories, score, num_comments, count) in enumerate(zip(df['arxiv_id'], df['updated'], df['title'], df['summary'], df['authors'], df['comment'], df['primary_category'], df['categories'], df['score'], df['num_comments'], df['count'])):
        trans_texts, trans_ts = dlc.get(arxiv_id, None)
        summary_texts = seg.segment(summary.replace('\n', ' ')[:2000])
        # only post new papers
        if not (twenty_three_hours_ago < datetime.fromisoformat(trans_ts)):
            continue
        is_new = True
        prev_tweet_id = post_to_twitter_first_page(api_v1, api_v2, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
        time.sleep(1)
        top_n_submissions = submission_df[submission_df['arxiv_id'].apply(lambda ids: arxiv_id in ids)].head(5)
        prev_tweet_id = post_to_twitter_tweets(api_v2, prev_tweet_id, arxiv_id, top_n_submissions)
        post_to_twitter_trans(api_v1, api_v2, prev_tweet_id, arxiv_id, title, authors, summary_texts, trans_texts)
        print('post_to_twitter: ', f'[{len(df)-i}/{len(df)}]')
        time.sleep(1)
    post_to_twitter_ranking(api_v1, api_v2, dlc, df)


def upload_first_page_to_bluesky(api: nanoatp.BskyAgent, arxiv_id, summary_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_filename = download_arxiv_pdf(arxiv_id, tmp_dir)
        first_page_filename = pdf_to_png(pdf_filename)
        if os.path.isfile(first_page_filename):
            return api.uploadImage(first_page_filename, summary_text)
    return None


def post_to_bluesky_first_page(api: nanoatp.BskyAgent, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories):
    text, summary_text = generate_twitter_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
    images = []
    image = upload_first_page_to_bluesky(api, arxiv_id, strip_tweet(summary_text, 300))
    if image:
        images.append(image)
    parent_post = None
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": strip_tweet(text, 300), "embed": embed}
        parent_post = api.post(record)
    except Exception as e:
        print(e)
    return parent_post


def post_to_bluesky_posts(api: nanoatp.BskyAgent, root_post, parent_post, arxiv_id, df):
    for i, (id, score, num_comments, created_utc) in enumerate(zip(df['id'], df['score'], df['num_comments'], df['created_utc'])):
        stats_md = f'{score} Upvotes, {num_comments} Comments'
        created_at_md = datetime.fromtimestamp(created_utc).strftime('%d %b %Y')
        url_md = f'http://reddit.com/{id}'
        text = f'({i+1}/{len(df)}) {stats_md}, {created_at_md}\n{url_md}\n'
        try:
            record = {"text": strip_tweet(text, 300), "reply": {"root": root_post, "parent": parent_post}}
            parent_post = api.post(record)
        except Exception as e:
            print(e)
        time.sleep(1)
    return parent_post


def upload_html_to_bluesky(api, filename, html_text, alt_text):
    with tempfile.TemporaryDirectory() as tmp_dir:
        abs_path = os.path.join(tmp_dir, filename)
        abs_path = html_to_image(html_text, abs_path)
        if os.path.isfile(abs_path):
            return api.uploadImage(abs_path, alt_text)
    return None


def post_to_bluesky_trans(api: nanoatp.BskyAgent, root_post, parent_post, arxiv_id, title, authors, summary_texts, trans_texts):
    html_text = generate_trans_html(arxiv_id, title, authors, trans_texts, summary_texts)
    trans_text = ''.join(trans_texts)
    images = []
    image = upload_html_to_bluesky(api, f'{arxiv_id}.trans.jpg', html_text, strip_tweet(trans_text, 300))
    if image:
        images.append(image)
    text = f'https://arxiv.org/abs/{arxiv_id}\n{trans_text}'
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": strip_tweet(text, 300), "reply": {"root": root_post, "parent": parent_post}, "embed": embed}
        parent_post = api.post(record)
    except Exception as e:
        print(e)


def post_to_bluesky_ranking(api: nanoatp.BskyAgent, dlc, df):
    title = f'Top {len(df)} most popular arXiv papers in the last 7 days'
    date = datetime.now(timezone.utc).strftime('%d %b %Y')
    images = []
    html_text = generate_top_n_html(title, date, df, dlc)
    rev_df = df[::-1]
    len_df = len(df)

    def fmt(item):
        return f'[{item[0]+1}/{len_df}] https://arxiv.org/abs/{item[1][0]}'

    metadata = '\n'.join(map(fmt, enumerate(zip(rev_df['arxiv_id']))))
    image = upload_html_to_bluesky(api, 'top_n.jpg', html_text, strip_tweet(metadata, 300))
    if image:
        images.append(image)
    text = title
    try:
        embed = {"$type": "app.bsky.embed.images#main", "images": images}
        record = {"text": strip_tweet(text, 300), "embed": embed}
        return api.post(record)
    except Exception as e:
        print(e)
        return None


def post_to_bluesky(api: nanoatp.BskyAgent, dlc, df, submission_df):
    df = df[::-1]  # reverse order
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    seg = pysbd.Segmenter(language='en', clean=False)
    for i, (arxiv_id, updated, title, summary, authors, primary_category, categories, score, num_comments, count) in enumerate(zip(df['arxiv_id'], df['updated'], df['title'], df['summary'], df['authors'], df['primary_category'], df['categories'], df['score'], df['num_comments'], df['count'])):
        trans_texts, trans_ts = dlc.get(arxiv_id, None)
        # only post new papers
        if not (twenty_three_hours_ago < datetime.fromisoformat(trans_ts)):
            continue
        summary_texts = seg.segment(summary.replace('\n', ' ')[:2000])
        is_new = True
        parent_post = post_to_bluesky_first_page(api, df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories)
        root_post = parent_post
        time.sleep(1)
        top_n_submissions = submission_df[submission_df['arxiv_id'].apply(lambda ids: arxiv_id in ids)].head(5)
        parent_post = post_to_bluesky_posts(api, root_post, parent_post, arxiv_id, top_n_submissions)
        post_to_bluesky_trans(api, root_post, parent_post, arxiv_id, title, authors, summary_texts, trans_texts)
        print('post_to_bluesky: ', f'[{len(df)-i}/{len(df)}]')
        time.sleep(1)
    post_to_bluesky_ranking(api, dlc, df)


def main():
    # settings
    query = 'selftext:arxiv.org'
    filter_days = 365
    deepl_target_lang = 'JA'
    deepl_expire_days = 30
    notify_top_n = int(os.getenv('NOTIFY_TOP_N', 5))

    # prepare apis
    gcs_bucket = storage.Client().bucket(os.getenv('GCS_BUCKET_NAME'))
    deepl_api = deepl.Translator(os.getenv('DEEPL_AUTH_KEY'))  # type: ignore
    slack_api = WebClient(os.getenv('SLACK_BOT_TOKEN'))
    slack_channel = os.getenv('SLACK_CHANNEL')
    tweepy_api_v2 = tweepy.Client(
        bearer_token=os.getenv('TWITTER_BEARER_TOKEN'),
        consumer_key=os.getenv('TWITTER_API_KEY'),
        consumer_secret=os.getenv('TWITTER_API_KEY_SECRET'),
        access_token=os.getenv('TWITTER_ACCESS_TOKEN'),
        access_token_secret=os.getenv('TWITTER_ACCESS_TOKEN_SECRET'),
        wait_on_rate_limit=True)
    # because media_upload is only available on api v1.
    tweepy_api_v1 = tweepy.API(
        tweepy.OAuth1UserHandler(
            consumer_key=os.getenv('TWITTER_API_KEY'),
            consumer_secret=os.getenv('TWITTER_API_KEY_SECRET'),
            access_token=os.getenv('TWITTER_ACCESS_TOKEN'),
            access_token_secret=os.getenv('TWITTER_ACCESS_TOKEN_SECRET')),
        wait_on_rate_limit=True)
    bluesky_api = nanoatp.BskyAgent()
    bluesky_api.login(os.getenv('ATP_IDENTIFIER') or "", os.getenv('ATP_PASSWORD') or "")

    # search reddit and measure popularity
    paper_df, submission_df = summarize(query)

    # filter by days
    filtered_df = filter_df(paper_df, top_n=notify_top_n, days=filter_days)
    # print(filtered_df.head(10))

    # translate summary text
    dlc = deeplcache.DeepLCache(deepl_api)
    try:
        dlc.load_from_gcs(gcs_bucket, 'deepl_cache.json.gz')
    except Exception as e:
        print(e)
    dlc = translate_arxiv(dlc, filtered_df, deepl_target_lang)
    dlc.clear_cache(expire_timedelta=timedelta(days=deepl_expire_days))
    dlc.save_to_gcs(gcs_bucket, 'deepl_cache.json.gz')

    # post
    # post_to_slack(slack_api, slack_channel, dlc, filtered_df, submission_df)

    post_to_bluesky(bluesky_api, dlc, filtered_df, submission_df)

    # post_to_twitter(tweepy_api_v1, tweepy_api_v2, dlc, filtered_df, submission_df)


if __name__ == '__main__':
    main()
