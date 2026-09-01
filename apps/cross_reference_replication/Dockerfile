FROM python

WORKDIR /app

COPY --from=docker:latest /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:latest /usr/local/bin/docker-compose /usr/local/bin/docker-compose

COPY requirements.txt .

RUN pip3 install -r requirements.txt

COPY run.py .

CMD ["python3", "run.py"]
