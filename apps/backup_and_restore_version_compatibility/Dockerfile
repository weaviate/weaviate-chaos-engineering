FROM python:3.10-slim-bullseye

RUN apt-get update && apt-get install -y git

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt


COPY backup_and_restore_version_compatibility.py ./
