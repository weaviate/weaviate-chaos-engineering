FROM python:3.11-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN apt-get update && apt-get install -y git
RUN pip3 install -r requirements.txt

COPY *.py ./
