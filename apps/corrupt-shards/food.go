//                           _       _
// __      _____  __ ___   ___  __ _| |_ ___
// \ \ /\ / / _ \/ _` \ \ / / |/ _` | __/ _ \
//  \ V  V /  __/ (_| |\ V /| | (_| | ||  __/
//   \_/\_/ \___|\__,_| \_/ |_|\__,_|\__\___|
//
//  Copyright © 2016 - 2023 Weaviate B.V. All rights reserved.
//
//  CONTACT: hello@weaviate.io
//

package main

import (
	"context"
	"fmt"
	"math/rand"

	"github.com/go-openapi/strfmt"
	"github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate/entities/models"
	"github.com/weaviate/weaviate/entities/schema"
)

const (
	PIZZA_QUATTRO_FORMAGGI_ID = "10523cdd-15a2-42f4-81fa-267fe92f7cd6"
)

var IdsByClass = map[string][]string{
	"Pizza": {
		PIZZA_QUATTRO_FORMAGGI_ID,
	},
}

var AllIds = []string{
	PIZZA_QUATTRO_FORMAGGI_ID,
}

// ##### SCHEMA #####

func CreateSchemaPizza(client *weaviate.Client) {
	createSchema(client, classPizza())
}

func createSchema(client *weaviate.Client, class *models.Class) {
	err := client.Schema().ClassCreator().
		WithClass(class).
		Do(context.Background())

	requireNil(err)
}

// ##### CLASSES #####

func classPizza() *models.Class {
	return &models.Class{
		Class:           "Pizza",
		Description:     "A delicious religion like food and arguably the best export of Italy.",
		VectorIndexType: "hnsw",
		Properties:      classPropertiesFood(),
		// one shard on all three nodes
		ShardingConfig: map[string]interface{}{
			"desiredCount": 1,
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor: 3,
		},
	}
}

func classPropertiesFood() []*models.Property {
	nameProperty := &models.Property{
		Name:        "name",
		Description: "name",
		DataType:    schema.DataTypeText.PropString(),
	}
	descriptionProperty := &models.Property{
		Name:        "description",
		Description: "description",
		DataType:    schema.DataTypeText.PropString(),
	}
	bestBeforeProperty := &models.Property{
		Name:        "best_before",
		Description: "You better eat this food before it expires",
		DataType:    schema.DataTypeDate.PropString(),
	}
	priceProperty := &models.Property{
		Name:        "price",
		Description: "price",
		DataType:    schema.DataTypeNumber.PropString(),
	}

	return []*models.Property{
		nameProperty, descriptionProperty, bestBeforeProperty, priceProperty,
	}
}

// ##### DATA #####

func CreateDataPizza(client *weaviate.Client, consistencyLevel string) {
	createData(
		client,
		[]*models.Object{
			objectPizzaQuattroFormaggi(),
		},
		consistencyLevel,
	)
}

func CreateDataPizzaRandom(client *weaviate.Client, consistencyLevel string) {
	for batchNum := 0; batchNum < 4; batchNum++ {
		batch := make([]*models.Object, 0)
		for i := 0; i < 100; i++ {
			batch = append(batch, objectPizzaRandom())
		}
		createData(client, batch, consistencyLevel)
	}
}

func GetOnePizza(client *weaviate.Client, objectID, consistencyLevel string) *models.Object {
	resp, err := client.
		Data().
		ObjectsGetter().
		WithClassName("Pizza").
		WithConsistencyLevel(consistencyLevel).
		WithID(objectID).
		WithVector().
		Do(context.TODO())

	requireNil(err)
	requireNotNil(resp)
	respLen := len(resp)
	requireTrue(respLen == 1, fmt.Sprintf("expected len of 1, actual: %d", respLen))
	pizza := resp[0]
	// fmt.Println("NATEE pizza", pizza)
	requireNotNil(pizza)
	requireTrue(pizza.ID == strfmt.UUID(objectID), fmt.Sprintf("ID expected: %s, actual: %s", objectID, pizza.ID))
	return pizza
}

func NearVectorPizza(client *weaviate.Client, objectID, consistencyLevel string) {
	// resultSet, gqlErr := client.GraphQL().
	// 	Get().
	// 	WithClassName("Pizza").
	// 	WithFields(graphql.Field{Name: "name"}).
	// 	WithConsistencyLevel(consistencyLevel).
	// 	Do(context.Background())
	// TODO consistencyLevel
	resultSet, gqlErr := client.GraphQL().
		Raw().
		WithQuery(fmt.Sprintf("{Get {Pizza (consistencyLevel: %s) {name _additional {vector}}}}", consistencyLevel)).
		Do(context.Background())
	requireNil(gqlErr)
	if len(resultSet.Errors) > 0 {
		for i, e := range resultSet.Errors {
			fmt.Println("near vector error: ", i, e)
		}
	}
	requireNil(resultSet.Errors)

	// get := resultSet.Data["Get"].(map[string]interface{})
	// pizzas := get["Pizza"].([]interface{})
	// requireTrue(len(pizzas) == 1, "len pizzas", fmt.Sprint(len(pizzas)))
	// fmt.Println("NATEE nearPizza", pizzas[0])
	// fmt.Println("NATEE nearPizza", pizzas[1])
}

func createData(client *weaviate.Client, objects []*models.Object, consistencyLevel string) {
	resp, err := client.Batch().ObjectsBatcher().
		WithObjects(objects...).
		WithConsistencyLevel(consistencyLevel). // TODO QUORUM?
		Do(context.Background())

	requireNil(err)
	requireNotNil(resp)
	requireTrue(len(resp) == len(objects), "expected len(resp) == len(objects)")
	for i := 0; i < len(resp); i++ {
		s := resp[i].Result.Status
		requireNotNil(s)
		requireTrue(*s == "SUCCESS", "*s == SUCCESS")
	}
}

// ##### OBJECTS #####

func objectPizzaQuattroFormaggi() *models.Object {
	return objectPizzaQuattroFormaggiWithId(PIZZA_QUATTRO_FORMAGGI_ID)
}

func objectPizzaQuattroFormaggiWithId(id strfmt.UUID) *models.Object {
	return &models.Object{
		Class: "Pizza",
		ID:    id,
		Properties: map[string]interface{}{
			"name":        "Quattro Formaggi",
			"description": "Pizza quattro formaggi Italian: [ˈkwattro forˈmaddʒi] (four cheese pizza) is a variety of pizza in Italian cuisine that is topped with a combination of four kinds of cheese, usually melted together, with (rossa, red) or without (bianca, white) tomato sauce. It is popular worldwide, including in Italy,[1] and is one of the iconic items from pizzerias's menus.",
			"price":       float32(1.1),
			"best_before": "2022-05-03T12:04:40+02:00",
		},
		Vector: randVector(1024),
	}
}

func randVector(n int) (v []float32) {
	v = make([]float32, 1024)
	for i := 0; i < n; i++ {
		v[i] = rand.Float32()
	}
	return
}

func objectPizzaRandom() *models.Object {
	return &models.Object{
		Class: "Pizza",
		Properties: map[string]interface{}{
			"name":        randStringRunes(32),
			"description": randStringRunes(256),
			"price":       rand.Float32(),
			"best_before": "2022-05-03T12:04:40+02:00",
		},
		Vector: randVector(1024),
	}
}

var letterRunes = []rune("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

func randStringRunes(n int) string {
	b := make([]rune, n)
	for i := range b {
		b[i] = letterRunes[rand.Intn(len(letterRunes))]
	}
	return string(b)
}
