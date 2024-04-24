FROM python:3.10-slim-bullseye

WORKDIR /workdir

ARG backend
ENV BACKUP_BACKEND_PROVIDER=${backend}

ARG expected_shard_count
ENV EXPECTED_SHARD_COUNT=${expected_shard_count}

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY backup_and_restore_multi_tenant.py ./
