FROM golang:1.21-alpine

WORKDIR /app

COPY go.mod go.sum ./

RUN go mod download

COPY run.go ./

RUN go build -o runner .

CMD ["/app/runner"]
