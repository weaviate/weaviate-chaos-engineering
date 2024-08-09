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
		log.Printf("Updating %s tenants from %s classes...", strconv.Itoa(numTenants), strconv.Itoa(numClasses))
		for i := 0; i < numClasses; i++ {

			class := returnClass(i)
			tenants, err := node1Client.Schema().TenantsGetter().WithClassName(class.Class).Do(ctx)
			if err != nil {
				log.Fatalf("failed to get tenants: %v", err)
			}

			log.Println("Updating %s tenants...", strconv.Itoa(len(tenants)))
			for _, tenant := range tenants {
				var desiredStatus string
				if tenant.ActivityStatus == "HOT" {
					desiredStatus = "COLD"
				} else {
					desiredStatus = "HOT"
				}
				err := node1Client.Schema().TenantsUpdater().WithClassName(class.Class).WithTenants(models.Tenant{Name: tenant.Name, ActivityStatus: desiredStatus}).Do(ctx)
				if err != nil {
					log.Fatalf("failed to update tenant %v: %v", tenant.Name, err)
				}
				// Sleep for a while to slow down the update process
				time.Sleep(1 * time.Second)
			}
		}
	}
}
