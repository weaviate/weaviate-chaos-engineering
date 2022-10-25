FROM python:3.9-slim-buster

WORKDIR /workdir

COPY requirements.txt .
COPY weaviate_client-3.9.0.dev0-py3-none-any.whl .

RUN pip3 install -r requirements.txt
RUN pip3 install weaviate_client-3.9.0.dev0-py3-none-any.whl


COPY delete_and_recreate.py ./
