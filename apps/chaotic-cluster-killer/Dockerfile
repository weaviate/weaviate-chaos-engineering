FROM python:3.11-alpine

WORKDIR /chaos

RUN ["apk", "add", "curl", "bash"]

COPY requirements.txt .
RUN ["pip3", "install", "-r", "requirements.txt"]

COPY run.sh .
COPY run.py .

COPY --from=docker:latest /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:latest /usr/local/bin/docker-compose /usr/local/bin/docker-compose

CMD ["bash", "/chaos/run.sh"]
