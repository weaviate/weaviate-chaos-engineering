FROM golang:1.19-alpine

WORKDIR /workdir

COPY go.mod go.sum ./

RUN go mod download

ARG APP_NAME
ENV APP_NAME ${APP_NAME}

COPY ./${APP_NAME}.go .
COPY ./shared.go .

RUN go build -o ${APP_NAME} .

CMD "/workdir/${APP_NAME}"
