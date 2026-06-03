FROM python:3.11-slim-bullseye

# The app lives in /app, NOT /workdir: the runner mounts the host results dir
# over /workdir (-v "$PWD/workdir:/workdir"), so anything baked into /workdir
# would be shadowed by the mount. /workdir is reserved for results.json.
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY bench.py ./

CMD ["python3", "/app/bench.py"]
