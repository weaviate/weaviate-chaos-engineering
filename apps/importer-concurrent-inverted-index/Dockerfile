FROM golang:1.19-alpine

WORKDIR /app

COPY go.mod go.sum ./

RUN go mod download

COPY run.go wordlist.go ./

RUN go build -o importer .

CMD ["/app/importer"]
