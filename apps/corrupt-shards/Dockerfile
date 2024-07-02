FROM golang:1.20-alpine

WORKDIR /app

COPY go.mod go.sum ./

RUN go mod download

COPY food.go run.go assert.go ./

RUN go build -o corrupt_shards .

ENTRYPOINT ["/app/corrupt_shards"]
