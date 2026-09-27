# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import os
import subprocess
import tempfile
import unicodedata
from shlex import quote

from weasyprint import HTML


def download_arxiv_pdf(arxiv_id: str, tmp_dir: str):
    dir = quote(tmp_dir)
    output = quote(f"{arxiv_id}.pdf")
    url = quote(f"https://arxiv.org/pdf/{arxiv_id}.pdf")
    result = subprocess.run(f"aria2c -q -x5 -k1M -d {dir} -o {output} {url}", shell=True)
    assert result.returncode == 0  # TODO
    return os.path.join(tmp_dir, f"{arxiv_id}.pdf")


def pdf_to_png(pdf_filename: str):
    filename = quote(pdf_filename)
    result = subprocess.run(f"pdftoppm -q -png -singlefile -scale-to-x 1200 -scale-to-y -1 {filename} {filename}", shell=True)
    assert result.returncode == 0  # TODO
    return f"{pdf_filename}.png"


def html_to_image(html: str, image_filename: str):
    base, ext = os.path.splitext(image_filename)
    if ext.lower() != ".png":
        raise ValueError(f"expected .png output, got {image_filename!r}")
    with tempfile.TemporaryDirectory() as td:
        pdf_path = os.path.join(td, "page.pdf")
        HTML(string=html).write_pdf(
            pdf_path,
            presentational_hints=True,
        )
        pdf_q = quote(pdf_path)
        out_q = quote(base)
        result = subprocess.run(
            f"pdftoppm -q -png -singlefile -scale-to-x 1200 -scale-to-y -1 {pdf_q} {out_q}",
            shell=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdftoppm failed with code {result.returncode}")
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


def avoid_auto_link(text: str):
    """replace period to one dot leader to avoid auto link.
    https://shkspr.mobi/blog/2015/01/how-to-stop-twitter-auto-linking-urls/"""
    return text.replace(".", "․")


def strip(text: str, length: int):
    return text[: length - 3] + "..." if len(text) > length else text


def get_link_type(link: str):
    match link:
        case x if x.find("reddit.com") != -1 or x.find("redd.it") != -1:
            return "Reddit"
        case x if x.find("news.ycombinator.com") != -1:
            return "Hacker News"
        case x if x.find("huggingface.co") != -1:
            return "Hugging Face"
        case x if x.find("alphaxiv.org") != -1:
            return "alphaXiv"
        case _:
            return ""
