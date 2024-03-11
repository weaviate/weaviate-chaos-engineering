FROM python:3.10-slim-bullseye

WORKDIR /workdir

ARG backend
ENV BACKUP_BACKEND_PROVIDER=${backend}

COPY requirements.txt .
RUN pip3 install -r requirements.txt


COPY backup_and_flush.py ./
