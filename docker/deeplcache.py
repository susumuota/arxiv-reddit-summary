# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import gzip
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import deepl


class DeepLCache:
    def __init__(self, translator: deepl.Translator):
        self.translator = translator
        self.cache: dict[str, tuple[list[str], str]] = {}

    def clear_cache(self, expire_timedelta: timedelta | None = None):
        if expire_timedelta is None:
            self.cache = {}
            return
        expire_dt = datetime.now(timezone.utc) - expire_timedelta

        def is_not_expire(item):  # item is [arxiv_id, [texts, ts]]
            return datetime.fromisoformat(item[1][1]) > expire_dt

        self.cache = dict(filter(is_not_expire, self.cache.items()))

    def __repr__(self):
        return repr(self.cache)  # TODO

    def load(self, filename: str):
        with gzip.open(filename, "rt", encoding="UTF-8") as f:
            self.cache = json.load(f)

    def save(self, filename: str):
        with gzip.open(filename, "wt", encoding="UTF-8") as f:
            json.dump(self.cache, f)

    def load_from_s3(self, s3_bucket, filename: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            s3_bucket.download_file(filename, tmpfilename)
            self.load(tmpfilename)

    def save_to_s3(self, s3_bucket, filename: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            self.save(tmpfilename)
            s3_bucket.upload_file(filename, tmpfilename)

    def load_from_gcs(self, gcs_bucket, filename: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            gcs_bucket.blob(filename).download_to_filename(tmpfilename)
            self.load(tmpfilename)

    def save_to_gcs(self, gcs_bucket, filename: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            self.save(tmpfilename)
            gcs_bucket.blob(filename).upload_from_filename(tmpfilename)

    def get(self, key: str, default=None):
        return self.cache.get(key, default)

    def translate_text(self, text: str | list[str], target_lang: str, key: str):
        trans = self.get(key, None)
        if trans is not None:
            return trans
        result = self.translator.translate_text(text=text, target_lang=target_lang)
        trans_texts = [r.text for r in result] if type(result) is list else [result.text] if type(result) is deepl.TextResult else []
        trans_ts = datetime.now(timezone.utc).isoformat()
        trans = (trans_texts, trans_ts)
        self.cache[key] = trans
        return trans
