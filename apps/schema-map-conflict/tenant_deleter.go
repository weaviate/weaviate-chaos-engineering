package main

import (
	"context"
	"log"
	"strconv"
	"time"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	{
		log.Printf("Deleting %s classes with %s tenants...", strconv.Itoa(numClasses), strconv.Itoa(numTenants))
		for i := 0; i < numClasses; i++ {

			class := returnClass(i)
			for j := 0; j < numTenants; j++ {

				log.Println("Deleting tenants...")
				err := node1Client.Schema().TenantsDeleter().WithClassName(class.Class).WithTenants("tenant" + strconv.Itoa(j)).Do(ctx)
				if err != nil {
					log.Fatalf("failed to delete tenants: %v", err)
				}
				// Sleep for a while to slow down the creation process
				time.Sleep(1 * time.Second)
			}

			err := node1Client.Schema().ClassDeleter().WithClassName(class.Class).Do(ctx)
			if err != nil {
				log.Fatalf("failed to delete class %s: %v", class.Class, err)
			}

		}
	}
}
