FROM python:3.10-slim

WORKDIR /workdir

COPY requirements.txt .
COPY weaviate_client-3.9.0b0-py3-none-any.whl .

RUN pip3 install -r requirements.txt
RUN pip3 install weaviate_client-3.9.0b0-py3-none-any.whl

COPY run.py ./
