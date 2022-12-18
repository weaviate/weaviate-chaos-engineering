FROM python:3.10-slim-bullseye
RUN apt-get update && apt-get install -y curl unzip

WORKDIR /workdir

COPY requirements.txt .
COPY weaviate_client-3.10.0b0-py3-none-any.whl . 
RUN pip3 install -r requirements.txt
RUN pip3 install weaviate_client-3.10.0b0-py3-none-any.whl

COPY run.py ./
