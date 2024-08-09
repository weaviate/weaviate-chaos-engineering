package main

import (
	"context"
	"log"
	"strconv"
	"time"

	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	{
		log.Println("Delete any existing data...")
		err := node1Client.Schema().AllDeleter().Do(ctx)
		if err != nil {
			log.Fatalf("failed to delete all: %v", err)
		}
	}

	{
		log.Printf("Creating %s classes with %s tenants...", strconv.Itoa(numClasses), strconv.Itoa(numTenants))
		for i := 0; i < numClasses; i++ {

			class := returnClass(i)
			err := node1Client.Schema().ClassCreator().WithClass(&class).Do(ctx)
			if err != nil {
				log.Fatalf("failed to create class %s: %v", class.Class, err)
			}

			for j := 0; j < numTenants; j++ {

				log.Println("Creating tenants...")
				err = node1Client.Schema().TenantsCreator().WithClassName(class.Class).WithTenants(models.Tenant{Name: "tenant" + strconv.Itoa(j)}).Do(ctx)
				if err != nil {
					log.Fatalf("failed to create tenants: %v", err)
				}
			}
		}
	}
}
