FROM python:3.11-slim-bullseye

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY bench.py ./

CMD ["python3", "bench.py"]
