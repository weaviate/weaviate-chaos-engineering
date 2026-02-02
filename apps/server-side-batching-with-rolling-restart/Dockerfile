FROM python:3.13-slim-bullseye

WORKDIR /workdir
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y git

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY *.py ./
