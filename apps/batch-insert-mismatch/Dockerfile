FROM python:3.10-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY wikipedia1k.json.zip ./
COPY batch-insert-mismatch.py ./
