FROM python:3.11-slim-bullseye

# to support pprof profiles
RUN apt-get update && apt-get install -y golang-go

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY . ./
