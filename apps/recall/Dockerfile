FROM python:3.10-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN apt-get update && apt-get install -y git
RUN pip3 install -r requirements.txt -f https://download.pytorch.org/whl/torch_stable.html

COPY generate.py import.py ./
