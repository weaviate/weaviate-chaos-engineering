FROM golang:1.21-alpine

WORKDIR /app

COPY go.mod ./

RUN go mod download

COPY main.go .

RUN go build -o analyzer .

CMD ["/app/analyzer"]