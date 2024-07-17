package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strconv"
	"time"

	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
)

func main() {
	args := os.Args
	if len(args) != 4 {
		panic("expected exactly two command line argument: [setup, query] and [portNumber (eg 8080)]")
	}
	stageToRun := args[1]
	portNumber, err := strconv.Atoi(args[2])
	consistencyLevel := args[3]
	if err != nil {
		panic(fmt.Sprintf("portNumber must be an int: %v", err))
	}
	switch stageToRun {
	case "setup":
		createPizzas(portNumber, consistencyLevel)
	case "query":
		queryPizzas(portNumber, consistencyLevel)
	default:
		panic("unexpected command line argument, expected one of [setup, query]: " + stageToRun)
	}
}

func queryPizzas(portNum int, consistencyLevel string) {
	cleanupClient, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: fmt.Sprintf("localhost:%d", portNum)})
	requireNil(err)
	defer cleanup(cleanupClient)
	// TODO i think we wait 10 seconds to return resp with QUORUM clientside if we don't hear back from one...
	for _, portNum := range []int{
		// TODO test on 1.26/main: Right now nodes can't tolerate corrupt clients on their own disk regardless of consistency level
		// 8080,
		// 8081,
		// 8082,
		portNum,
	} {
		cl := consistencyLevel
		fmt.Println("querying with on port number:", portNum, "with consistency level:", cl)
		client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: fmt.Sprintf("localhost:%d", portNum)})
		requireNil(err)
		GetOnePizza(client, PIZZA_QUATTRO_FORMAGGI_ID, cl)
		NearVectorPizza(client, PIZZA_QUATTRO_FORMAGGI_ID, cl)
	}
	log.Println("queryPizzas finished. OK")
}

func createPizzas(portNum int, consistencyLevel string) {

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: fmt.Sprintf("localhost:%d", portNum)})
	requireNil(err)
	cleanup(client)

	log.Println("creating pizzas")

	// TODO flashcard creating/getting schema/data in go
	CreateSchemaPizza(client)
	CreateDataPizza(client, consistencyLevel)
	CreateDataPizzaRandom(client, consistencyLevel)

	// assume the LSM is configured to sync to disk within 2 seconds
	time.Sleep(time.Second * 2)

	log.Println("createPizzas finished. OK")
}

func cleanup(client *wvt.Client) {
	err := client.Schema().AllDeleter().Do(context.Background())
	requireNil(err)
}
