FROM python:3.11-alpine

WORKDIR /chaos

RUN ["apk", "add", "curl", "bash"]

COPY run.sh .

COPY --from=docker:latest /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:latest /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

CMD ["bash", "/chaos/run.sh"]
