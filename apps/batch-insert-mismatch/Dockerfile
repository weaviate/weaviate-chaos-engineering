FROM python:3.10-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN apt-get update && apt-get install -y git
RUN pip3 install -r requirements.txt

COPY wikipedia1k.json.zip ./
COPY batch-insert-mismatch.py ./
