package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
)

func main() {
	args := os.Args
	if len(args) != 2 {
		panic("expected exactly one command line argument: [setup, query]")
	}
	stageToRun := args[1]
	switch stageToRun {
	case "setup":
		createPizzas()
	case "query":
		queryPizzas()
	default:
		panic("unexpected command line argument, expected one of [setup, query]: " + stageToRun)
	}
}

func queryPizzas() {
	cleanupClient, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: "localhost:8080"})
	requireNil(err)
	defer cleanup(cleanupClient)
	// TODO i think we wait 10 seconds to return resp with QUORUM clientside if we don't hear back from one...
	for _, portNum := range []int{
		// TODO test on 1.26/main: Right now nodes can't tolerate corrupt clients on their own disk regardless of consistency level
		// 8080,
		8081,
		8082,
	} {
		fmt.Println("querying with on port number:", portNum)
		client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: fmt.Sprintf("localhost:%d", portNum)})
		requireNil(err)
		GetOnePizza(client, PIZZA_QUATTRO_FORMAGGI_ID, "QUORUM")
	}
	log.Println("queryPizzas finished. OK")
}

func createPizzas() {

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: "localhost:8080"})
	requireNil(err)
	cleanup(client)

	log.Println("creating pizzas")

	// TODO flashcard creating/getting schema/data in go
	CreateSchemaPizza(client)
	CreateDataPizza(client)

	// assume the LSM is configured to sync to disk within 2 seconds
	time.Sleep(time.Second * 2)

	log.Println("createPizzas finished. OK")
}

func cleanup(client *wvt.Client) {
	err := client.Schema().AllDeleter().Do(context.Background())
	requireNil(err)
}
