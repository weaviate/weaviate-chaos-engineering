FROM python:3.10-slim-bookworm

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY objects-are-not-deleted.py ./
