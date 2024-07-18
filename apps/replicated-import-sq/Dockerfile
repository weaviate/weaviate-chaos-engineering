FROM python:3.10-slim-bullseye
RUN apt-get update && apt-get install -y curl unzip git

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY run.py ./
