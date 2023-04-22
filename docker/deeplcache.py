# SPDX-FileCopyrightText: 2023 Susumu OTA <1632335+susumuota@users.noreply.github.com>
# SPDX-License-Identifier: MIT

import datetime
import gzip
import json
import os
import tempfile


class DeepLCache:
    def __init__(self, translator):
        self.translator = translator
        self.cache = {}

    def clear_cache(self, expire_timedelta=None):
        if expire_timedelta is None:
            self.cache = {}
            return
        expire_dt = datetime.datetime.now(datetime.timezone.utc) - expire_timedelta

        def is_not_expire(item):
            # item is [arxiv_id, [texts, ts]]
            return datetime.datetime.fromisoformat(item[1][1]) > expire_dt

        self.cache = dict(filter(is_not_expire, self.cache.items()))

    def __repr__(self):
        return repr(self.cache)  # TODO

    def load(self, filename):
        with gzip.open(filename, "rt", encoding="UTF-8") as f:
            self.cache = json.load(f)

    def save(self, filename):
        with gzip.open(filename, "wt", encoding="UTF-8") as f:
            json.dump(self.cache, f)  # type: ignore

    def load_from_s3(self, s3_bucket, filename):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            s3_bucket.download_file(filename, tmpfilename)
            self.load(tmpfilename)

    def save_to_s3(self, s3_bucket, filename):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            self.save(tmpfilename)
            s3_bucket.upload_file(filename, tmpfilename)

    def load_from_gcs(self, gcs_bucket, filename):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            gcs_bucket.blob(filename).download_to_filename(tmpfilename)
            self.load(tmpfilename)

    def save_to_gcs(self, gcs_bucket, filename):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfilename = os.path.join(tmpdir, filename)
            self.save(tmpfilename)
            gcs_bucket.blob(filename).upload_from_filename(tmpfilename)

    def get(self, key, default=None):
        return self.cache.get(key, default)

    def translate_text(self, text, target_lang, key):
        trans = self.get(key, None)
        if trans is not None:
            return trans
        result = self.translator.translate_text(text=text, target_lang=target_lang)
        trans_texts = [r.text for r in result] if type(text) is list else result.text
        trans_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        trans = [trans_texts, trans_ts]
        self.cache[key] = trans
        return trans
