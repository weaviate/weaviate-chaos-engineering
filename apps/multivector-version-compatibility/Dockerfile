FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y git

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY *.py ./
