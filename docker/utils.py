# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import os
import re
import subprocess
import unicodedata
from shlex import quote

import dateutil.parser
import imgkit


def download_arxiv_pdf(arxiv_id, tmp_dir):
    dir = quote(tmp_dir)
    output = quote(f"{arxiv_id}.pdf")
    url = quote(f"https://arxiv.org/pdf/{arxiv_id}.pdf")
    result = subprocess.run(f"aria2c -q -x5 -k1M -d {dir} -o {output} {url}", shell=True)
    assert result.returncode == 0  # TODO
    return os.path.join(tmp_dir, f"{arxiv_id}.pdf")


def pdf_to_png(pdf_filename):
    filename = quote(pdf_filename)
    result = subprocess.run(f"pdftoppm -q -png -singlefile -scale-to-x 1200 -scale-to-y -1 {filename} {filename}", shell=True)
    assert result.returncode == 0  # TODO
    return f"{pdf_filename}.png"


def html_to_image(html, image_filename):
    result = imgkit.from_string(html, image_filename, options={"width": 1200, "quiet": ""})
    assert result is True  # TODO
    return image_filename


def get_char_width(c: str):
    return 2 if unicodedata.east_asian_width(c) in "FWA" else 1


def len_tweet(text: str):
    return sum(map(get_char_width, text))


def strip_tweet(text: str, max_length=280, dots="..."):
    length = max_length - (len(dots) if dots else 0)
    buf = []
    count = 0
    for c in text:
        width = get_char_width(c)
        if count + width > length:
            return "".join(buf) + (dots if dots else "")
        buf.append(c)
        count += width
    return text


def generate_first_page(df, i, is_new, arxiv_id, updated, title, summary_texts, authors, score, num_comments, count, primary_category, categories):
    summary_text = " ".join(summary_texts)
    new_md = "🆕" if is_new else ""
    authors_md = ", ".join(authors)
    categories_md = avoid_auto_link(" | ".join([c for c in [primary_category] + [c for c in categories if c != primary_category and re.match(r"\w+\.\w+$", c)]]))
    stats_md = f"{score} Upvotes, {num_comments} Comments, {count} Posts"
    updated_md = dateutil.parser.isoparse(updated).strftime("%d %b %Y")
    title_md = title
    abs_md = f"https://arxiv.org/abs/{arxiv_id}"
    text = f"[{len(df)-i}/{len(df)}] {stats_md}\n{abs_md} {categories_md}, {updated_md}\n\n{new_md}{title_md}\n\n{authors_md}"
    return text, summary_text


def avoid_auto_link(text):
    """replace period to one dot leader to avoid auto link.
    https://shkspr.mobi/blog/2015/01/how-to-stop-twitter-auto-linking-urls/"""
    return text.replace(".", "․")


def strip(text: str, length: int):
    return text[: length - 3] + "..." if len(text) > length else text
