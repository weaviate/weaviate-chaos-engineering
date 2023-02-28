FROM python:3.10-slim-bullseye
RUN apt-get update && apt-get install -y curl unzip git

WORKDIR /workdir

COPY requirements.txt .
COPY weaviate_client-3.10.0b0-py3-none-any.whl . 
RUN pip3 install -r requirements.txt
RUN python -m pip install  "weaviate-client @ git+https://github.com/weaviate/weaviate-python-client.git"

COPY run.py ./
