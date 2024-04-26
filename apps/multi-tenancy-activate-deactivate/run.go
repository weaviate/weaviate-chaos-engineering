package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"time"

	"github.com/go-openapi/strfmt"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/fault"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	log.Println("TEST 2 starting")

	loops := 50
	classPizza := "Pizza"
	// classSoup := "Soup"
	// classRisotto := "Risotto"

	uuidCounter := uint32(0)
	getId := func() strfmt.UUID {
		uuidCounter++
		return strfmt.UUID(fmt.Sprintf("00000000-0000-0000-0000-%012x", uuidCounter-1))
	}
	nextTenantId := 0
	name := func(id int) string {
		return fmt.Sprintf("tenant_%d", id)
	}

	ctx := context.Background()
	r := rand.New(rand.NewSource(time.Now().UnixNano()))

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: "host.docker.internal:8080"})
	requireNil(err)

	cleanup := func() {
		err := client.Schema().AllDeleter().Do(context.Background())
		requireNil(err)
	}
	cleanup()
	defer cleanup()

	// ==================================================================================

	log.Println("creating MT classes")

	CreateSchemaPizzaForTenants(client)
	// CreateSchemaSoupForTenants(client)
	// CreateSchemaRisottoForTenants(client)

	coldBuf := make(Tenants, 10)
	hotBuf := make(Tenants, 10)
	hotWithDataBuf := make(Tenants, 10)
	var allTenants Tenants

	for l := 1; l <= loops; l++ {
		log.Printf("loop [%d/%d] started\n", l, loops)

		for i := 0; i < 10; i++ {
			coldBuf[i] = models.Tenant{
				Name:           name(nextTenantId),
				ActivityStatus: models.TenantActivityStatusCOLD,
			}
			nextTenantId++

			hotBuf[i] = models.Tenant{
				Name:           name(nextTenantId),
				ActivityStatus: models.TenantActivityStatusHOT,
			}
			nextTenantId++

			hotWithDataBuf[i] = models.Tenant{
				Name:           name(nextTenantId),
				ActivityStatus: models.TenantActivityStatusHOT,
			}
			nextTenantId++
		}

		batchTenants := coldBuf.Merge(hotBuf).Merge(hotWithDataBuf)
		allTenants = append(allTenants, batchTenants...)

		// ==================================================================================

		log.Printf("loop [%d] creating tenants and populating 1/3 of them\n", l)

		CreateTenantsPizza(client, batchTenants...)
		// CreateTenantsSoup(client, batchTenants...)
		// CreateTenantsRisotto(client, batchTenants...)

		// // TODO test with interval
		// time.Sleep(2 * time.Second)

		CreateDataPizzaForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)
		// CreateDataSoupForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)
		// CreateDataRisottoForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)

		// ==================================================================================

		log.Printf("loop [%d] activating tenants created as inactive\n", l)

		// activate created as inactive
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// err = client.Schema().TenantsUpdater().
		// 	WithClassName(classSoup).
		// 	WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
		// 	Do(ctx)
		// requireNil(err)
		// err = client.Schema().TenantsUpdater().
		// 	WithClassName(classRisotto).
		// 	WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
		// 	Do(ctx)
		// requireNil(err)

		// ==================================================================================

		log.Printf("loop [%d] deactivating created tenants and activating them again (x3) \n", l)

		// deactivate and activate back again (x3)
		for i := 0; i < 3; i++ {
			err := client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)

			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)
			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)

			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
			// 	WithClassName(classRisotto).
			// 	Do(ctx)
			// requireNil(err)
			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
			// 	WithClassName(classRisotto).
			// 	Do(ctx)
			// requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// ==================================================================================

		log.Printf("loop [%d] verifying created tenants active\n", l)

		gotTenantsPizza, err := client.Schema().TenantsGetter().
			WithClassName(classPizza).
			Do(ctx)
		requireNil(err)

		gotBatchTenantsPizza := Tenants(gotTenantsPizza).ByNames(batchTenants.Names()...)
		requireTrue(len(gotBatchTenantsPizza) == 30, "len(gotBatchTenantsPizza) == 30")
		requireTrue(gotBatchTenantsPizza.IsStatus(models.TenantActivityStatusHOT),
			"gotBatchTenantsPizza.IsStatus(models.TenantActivityStatusHOT)")

		// gotTenantsSoup, err := client.Schema().TenantsGetter().
		// 	WithClassName(classPizza).
		// 	Do(ctx)
		// requireNil(err)

		// gotBatchTenantsSoup := Tenants(gotTenantsSoup).ByNames(batchTenants.Names()...)
		// requireTrue(len(gotBatchTenantsSoup) == 30, "len(gotBatchTenantsSoup) == 30")
		// requireTrue(gotBatchTenantsSoup.IsStatus(models.TenantActivityStatusHOT),
		// 	"gotBatchTenantsSoup.IsStatus(models.TenantActivityStatusHOT)")

		// gotTenantsRisotto, err := client.Schema().TenantsGetter().
		// 	WithClassName(classRisotto).
		// 	Do(ctx)
		// requireNil(err)

		// gotBatchTenantsRisotto := Tenants(gotTenantsRisotto).ByNames(batchTenants.Names()...)
		// requireTrue(len(gotBatchTenantsRisotto) == 30, "len(gotBatchTenantsRisotto) == 30")
		// requireTrue(gotBatchTenantsRisotto.IsStatus(models.TenantActivityStatusHOT),
		// 	"gotBatchTenantsRisotto.IsStatus(models.TenantActivityStatusHOT)")

		half := len(allTenants) / 2
		r.Shuffle(len(allTenants), func(i, j int) {
			allTenants[i], allTenants[j] = allTenants[j], allTenants[i]
		})

		// ==================================================================================

		log.Printf("loop [%d] activating 1st half of ALL tenants (act + 3x(deact + act))\n", l)

		// effectively activate and  populate 1st half
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// err = client.Schema().TenantsUpdater().
		// 	WithClassName(classSoup).
		// 	WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
		// 	Do(ctx)
		// requireNil(err)

		for i := 0; i < 3; i++ {
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)

			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)
			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// ==================================================================================

		log.Printf("loop [%d] populating 1st half of ALL tenants\n", l)

		// // TODO test with interval
		// time.Sleep(2 * time.Second)

		CreateDataPizzaForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		// CreateDataSoupForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		// CreateDataRisottoForTenantsWithIds(client, getId, allTenants[:half].Names()...)

		// ==================================================================================

		log.Printf("loop [%d] deactivating 2nd half of ALL tenants (3x(deact + act) + deact) \n", l)

		// effectively deactivate 2nd half
		for i := 0; i < 3; i++ {
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)

			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)
			// err = client.Schema().TenantsUpdater().
			// 	WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
			// 	WithClassName(classSoup).
			// 	Do(ctx)
			// requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			Do(ctx)
		requireNil(err)
		// err = client.Schema().TenantsUpdater().
		// 	WithClassName(classSoup).
		// 	WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
		// 	Do(ctx)
		// requireNil(err)

		log.Printf("loop [%d/%d] finished\n", l, loops)
	}

	log.Println("TEST 2 finished. OK")
}

func assertTenantActive(client *wvt.Client, className, tenantName string) {
	gotTenants, err := client.Schema().TenantsGetter().
		WithClassName(className).
		Do(context.Background())
	requireNil(err)
	requireTrue(len(gotTenants) > 0, "len(gotTenants) > 0")

	byName := Tenants(gotTenants).ByName(tenantName)
	requireNotNil(byName)
	requireTrue(models.TenantActivityStatusHOT == byName.ActivityStatus, "models.TenantActivityStatusHOT == byName.ActivityStatus")
}

func assertTenantInactive(client *wvt.Client, className, tenantName string) {
	gotTenants, err := client.Schema().TenantsGetter().
		WithClassName(className).
		Do(context.Background())
	requireNil(err)
	requireNotNil(gotTenants)
	requireTrue(len(gotTenants) > 0, "len(gotTenants) > 0")

	byName := Tenants(gotTenants).ByName(tenantName)
	requireNotNil(byName)
	requireTrue(models.TenantActivityStatusCOLD == byName.ActivityStatus, "models.TenantActivityStatusCOLD == byName.ActivityStatus")
}

func assertActiveTenantObjects(client *wvt.Client, className, tenantName string, expectedIds []string) {
	objects, err := client.Data().ObjectsGetter().
		WithClassName(className).
		WithTenant(tenantName).
		Do(context.Background())

	requireNil(err)
	requireNotNil(objects)
	requireTrue(len(objects) == len(expectedIds), "len(objects) == len(expectedIds)")

	for _, expectedId := range expectedIds {
		found := false
		for _, object := range objects {
			if expectedId == string(object.ID) {
				found = true
				break
			}
		}
		requireTrue(found, "found "+expectedId)
	}
}

func assertInactiveTenantObjects(client *wvt.Client, className, tenantName string) {
	objects, err := client.Data().ObjectsGetter().
		WithClassName(className).
		WithTenant(tenantName).
		Do(context.Background())

	requireNotNil(err)
	clientErr := err.(*fault.WeaviateClientError)
	requireTrue(422 == clientErr.StatusCode, "422 == clientErr.StatusCode")
	requireContains(clientErr.Msg, "tenant not active")
	requireNil(objects)
}
