FROM python

WORKDIR /app

COPY requirements.txt .

RUN pip3 install -r requirements.txt

COPY run.py .

CMD ["python3", "run.py"]
