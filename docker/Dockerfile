# SPDX-FileCopyrightText: 2022 Susumu OTA <1632335+susumuota@users.noreply.github.com>
#
# SPDX-License-Identifier: MIT

FROM python:3.10.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
  aria2 \
  fonts-ipafont-gothic \
  poppler-utils \
  wkhtmltopdf \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

CMD [ "python", "-u", "main.py" ]
