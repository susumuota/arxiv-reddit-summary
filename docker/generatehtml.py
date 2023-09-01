# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import re
from datetime import datetime, timedelta, timezone
from html import escape
from itertools import zip_longest

import dateutil.parser
import deeplcache
import pandas as pd

HTML_TRANS_TEMPLATE = """
<html>
  <head>
    <meta charset="utf-8">
    <style>
      body {{
        font-size: 24px;
        margin: 2em;
      }}
      .translation {{
        color: black;
      }}
      .source {{
        color: blue;
      }}
    </style>
  </head>
  <body>
    <span>{url}</span>
    <h2>
      {title}
    </h2>
    <h4>
      {authors}
    </h4>
    <div>
      {content}
    </div>
  </body>
</html>
"""

HTML_TRANS_ITEM_TEMPLATE = """
<p class="item">
  <span class="translation">
    {translation}
  </span>
  <br />
  <span class="source">
    {source}
  </span>
</p>
"""


def generate_trans_html(arxiv_id: str, title: str, authors: list[str], trans_texts: list[str], summary_texts: list[str]):
    authors_md = escape(", ".join(authors))
    title_md = escape(title)
    url_md = f"https://arxiv.org/abs/{arxiv_id}"
    items = map(
        lambda item: HTML_TRANS_ITEM_TEMPLATE.format(translation=escape(item[0]), source=escape(item[1])),
        zip_longest(trans_texts, summary_texts, fillvalue=""),
    )
    return HTML_TRANS_TEMPLATE.format(title=title_md, authors=authors_md, url=url_md, content="\n".join(items))


HTML_TOP_N_TEMPLATE = """
<html>
  <head>
    <meta charset="utf-8">
    <style>
      body {{
        font-size: 24px;
        margin: 2em;
      }}
      .new {{
        color: blue;
      }}
    </style>
  </head>
  <body>
    <span>{date}</span>
    <h2>
      {title}
    </h2>
    <div>
      {content}
    </div>
  </body>
</html>
"""

HTML_TOP_N_ITEM_TEMPLATE = """
<p class="item">
  [{i}/{n}] <b>{title}</b><br />
  {stats}, {categories}, {updated}
</p>
"""


def generate_top_n_html(page_title: str, date: str, df: pd.DataFrame, dlc: deeplcache.DeepLCache):
    page_title = escape(page_title)
    df = df[::-1]  # normal order (reversed reversed order)
    items = []
    twenty_three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=23)
    for i, (arxiv_id, updated, title, primary_category, categories, score, num_comments, count) in enumerate(zip(df["arxiv_id"], df["updated"], df["title"], df["primary_category"], df["categories"], df["score"], df["num_comments"], df["count"])):
        title = escape(title)
        trans = dlc.get(arxiv_id, None)
        if trans is None:
            continue
        _, trans_ts = trans
        if twenty_three_hours_ago < datetime.fromisoformat(trans_ts):
            title = f'<span class="new">[New] {title}</span>'
        categories = " | ".join([primary_category] + [c for c in categories if c != primary_category and re.match(r"\w+\.\w+$", c)])
        stats = f"<b>{score}</b> Likes, {num_comments} Comments, {count} Posts"
        updated = dateutil.parser.isoparse(updated).strftime("%d %b %Y")
        items.append(HTML_TOP_N_ITEM_TEMPLATE.format(i=(i+1), n=len(df), title=title, stats=stats, categories=categories, updated=updated, arxiv_id=arxiv_id))
    return HTML_TOP_N_TEMPLATE.format(title=page_title, date=date, content="\n".join(items))
