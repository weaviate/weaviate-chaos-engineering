FROM python:3.10-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY import.py ./
