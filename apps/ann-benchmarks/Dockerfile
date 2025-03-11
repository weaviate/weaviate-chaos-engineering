FROM python:3.11-slim-bullseye

# to support pprof profiles and HDF5
RUN apt-get update && apt-get install -y \
    golang-go \
    pkg-config \
    libhdf5-dev \
    libhdf5-serial-dev \
    hdf5-tools \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workdir

COPY requirements.txt .
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

COPY . ./
