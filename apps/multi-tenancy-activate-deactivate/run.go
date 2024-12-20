package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"math/rand"
	"strings"
	"sync"
	"time"

	"github.com/go-openapi/strfmt"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/fault"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	test1()
	test2()
}

func test1() {
	log.Println("TEST 1 starting")

	classPizza := "Pizza"
	classSoup := "Soup"
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: "localhost:8080"})
	requireNil(err)

	cleanup := func() {
		err := client.Schema().AllDeleter().Do(context.Background())
		requireNil(err)
	}
	cleanup()
	defer cleanup()

	createTenants := func(className string, groupId, count int, status string) Tenants {
		tenants := make(Tenants, count)
		for i := 0; i < count; i++ {
			tenants[i] = models.Tenant{
				Name:           fmt.Sprintf("tenant_%s_%d_%d", className, groupId, i),
				ActivityStatus: status,
			}
		}
		return tenants
	}
	assertActiveTenants := func(tenants Tenants, className string, expectedIds []string) {
		for _, tenant := range tenants {
			assertTenantActive(client, className, tenant.Name)
			assertActiveTenantObjects(client, className, tenant.Name, expectedIds)
		}
	}
	assertInactiveTenants := func(tenants Tenants, className string) {
		for _, tenant := range tenants {
			assertTenantInactive(client, className, tenant.Name)
			assertInactiveTenantObjects(client, className, tenant.Name)
		}
	}

	// ==================================================================================

	log.Println("create tenants (1,2,3), populate active tenants (1,2)")

	tenants1Pizza := createTenants(classPizza, 1, 5, "") // default status HOT
	tenants2Pizza := createTenants(classPizza, 2, 4, models.TenantActivityStatusHOT)
	tenants3Pizza := createTenants(classPizza, 3, 3, models.TenantActivityStatusCOLD)
	tenants1Soup := createTenants(classSoup, 1, 4, "") // default status HOT
	tenants2Soup := createTenants(classSoup, 2, 3, models.TenantActivityStatusHOT)
	tenants3Soup := createTenants(classSoup, 3, 2, models.TenantActivityStatusCOLD)
	idsPizza := IdsByClass[classPizza]
	idsSoup := IdsByClass[classSoup]

	CreateSchemaPizzaForTenants(client)
	CreateTenantsPizza(client, tenants1Pizza...)
	CreateTenantsPizza(client, tenants2Pizza...)
	CreateTenantsPizza(client, tenants3Pizza...)
	CreateDataPizzaForTenants(client, tenants1Pizza.Names()...)
	CreateDataPizzaForTenants(client, tenants2Pizza.Names()...)

	CreateSchemaSoupForTenants(client)
	CreateTenantsSoup(client, tenants1Soup...)
	CreateTenantsSoup(client, tenants2Soup...)
	CreateTenantsSoup(client, tenants3Soup...)
	CreateDataSoupForTenants(client, tenants1Soup.Names()...)
	CreateDataSoupForTenants(client, tenants2Soup.Names()...)

	assertActiveTenants(tenants1Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants2Pizza, classPizza, idsPizza)
	assertInactiveTenants(tenants3Pizza, classPizza)

	assertActiveTenants(tenants1Soup, classSoup, idsSoup)
	assertActiveTenants(tenants2Soup, classSoup, idsSoup)
	assertInactiveTenants(tenants3Soup, classSoup)

	// ==================================================================================

	log.Println("deactivate tenants (1)")

	tenants := make(Tenants, len(tenants1Pizza))
	for i, tenant := range tenants1Pizza {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	tenants = make(Tenants, len(tenants1Soup))
	for i, tenant := range tenants1Soup {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	assertInactiveTenants(tenants1Pizza, classPizza)
	assertActiveTenants(tenants2Pizza, classPizza, idsPizza)
	assertInactiveTenants(tenants3Pizza, classPizza)

	assertInactiveTenants(tenants1Soup, classSoup)
	assertActiveTenants(tenants2Soup, classSoup, idsSoup)
	assertInactiveTenants(tenants3Soup, classSoup)

	// ==================================================================================

	log.Println("activate and populate tenants (3)")

	tenants = make(Tenants, len(tenants3Pizza))
	for i, tenant := range tenants3Pizza {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	CreateDataPizzaForTenants(client, tenants3Pizza.Names()...)

	tenants = make(Tenants, len(tenants3Soup))
	for i, tenant := range tenants3Soup {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	CreateDataSoupForTenants(client, tenants3Soup.Names()...)

	assertInactiveTenants(tenants1Pizza, classPizza)
	assertActiveTenants(tenants2Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants3Pizza, classPizza, idsPizza)

	assertInactiveTenants(tenants1Soup, classSoup)
	assertActiveTenants(tenants2Soup, classSoup, idsSoup)
	assertActiveTenants(tenants3Soup, classSoup, idsSoup)

	// ==================================================================================

	log.Println("activate tenants (1)")

	tenants = make(Tenants, len(tenants1Pizza))
	for i, tenant := range tenants1Pizza {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	tenants = make(Tenants, len(tenants1Soup))
	for i, tenant := range tenants1Soup {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	assertActiveTenants(tenants1Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants2Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants3Pizza, classPizza, idsPizza)

	assertActiveTenants(tenants1Soup, classSoup, idsSoup)
	assertActiveTenants(tenants2Soup, classSoup, idsSoup)
	assertActiveTenants(tenants3Soup, classSoup, idsSoup)

	// ==================================================================================

	log.Println("deactivate tenants (2)")
	tenants = make(Tenants, len(tenants2Pizza))
	for i, tenant := range tenants2Pizza {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	tenants = make(Tenants, len(tenants2Soup))
	for i, tenant := range tenants2Soup {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	assertActiveTenants(tenants1Pizza, classPizza, idsPizza)
	assertInactiveTenants(tenants2Pizza, classPizza)
	assertActiveTenants(tenants3Pizza, classPizza, idsPizza)

	assertActiveTenants(tenants1Soup, classSoup, idsSoup)
	assertInactiveTenants(tenants2Soup, classSoup)
	assertActiveTenants(tenants3Soup, classSoup, idsSoup)

	// ==================================================================================

	log.Println("activate already active (1,3), deactivate already inactive (2), nothing changed")
	tenants = make(Tenants, 0, len(tenants1Pizza)+len(tenants2Pizza)+len(tenants3Pizza))
	for _, tenant := range tenants1Pizza {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		})
	}
	for _, tenant := range tenants2Pizza {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		})
	}
	for _, tenant := range tenants3Pizza {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		})
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	tenants = make(Tenants, 0, len(tenants1Soup)+len(tenants2Soup)+len(tenants3Soup))
	for _, tenant := range tenants1Soup {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		})
	}
	for _, tenant := range tenants2Soup {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusCOLD,
		})
	}
	for _, tenant := range tenants3Soup {
		tenants = append(tenants, models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		})
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	assertActiveTenants(tenants1Pizza, classPizza, idsPizza)
	assertInactiveTenants(tenants2Pizza, classPizza)
	assertActiveTenants(tenants3Pizza, classPizza, idsPizza)

	assertActiveTenants(tenants1Soup, classSoup, idsSoup)
	assertInactiveTenants(tenants2Soup, classSoup)
	assertActiveTenants(tenants3Soup, classSoup, idsSoup)

	// ==================================================================================

	log.Println("activate tenants (2)")
	tenants = make(Tenants, len(tenants2Pizza))
	for i, tenant := range tenants2Pizza {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	tenants = make(Tenants, len(tenants2Soup))
	for i, tenant := range tenants2Soup {
		tenants[i] = models.Tenant{
			Name:           tenant.Name,
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	err = client.Schema().TenantsUpdater().
		WithClassName(classSoup).
		WithTenants(tenants...).
		Do(ctx)
	requireNil(err)

	assertActiveTenants(tenants1Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants2Pizza, classPizza, idsPizza)
	assertActiveTenants(tenants3Pizza, classPizza, idsPizza)

	assertActiveTenants(tenants1Soup, classSoup, idsSoup)
	assertActiveTenants(tenants2Soup, classSoup, idsSoup)
	assertActiveTenants(tenants3Soup, classSoup, idsSoup)

	log.Println("TEST 1 finished. OK")
}

func test2() {
	log.Println("TEST 2 starting")

	loops := 50
	classPizza := "Pizza"
	classSoup := "Soup"
	classRisotto := "Risotto"

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

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: "localhost:8080"})
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
	CreateSchemaSoupForTenants(client)
	CreateSchemaRisottoForTenants(client)

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
		CreateTenantsSoup(client, batchTenants...)
		CreateTenantsRisotto(client, batchTenants...)
		CreateDataPizzaForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)
		CreateDataSoupForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)
		CreateDataRisottoForTenantsWithIds(client, getId, hotWithDataBuf.Names()...)

		// ==================================================================================

		log.Printf("loop [%d] activating tenants created as inactive\n", l)

		// activate created as inactive
		err = updateTentantWithRetry(client, classPizza, coldBuf.WithStatus(models.TenantActivityStatusHOT))
		requireNil(err)
		err = updateTentantWithRetry(client, classSoup, coldBuf.WithStatus(models.TenantActivityStatusHOT))
		requireNil(err)
		err = updateTentantWithRetry(client, classRisotto, coldBuf.WithStatus(models.TenantActivityStatusHOT))
		requireNil(err)

		// ==================================================================================

		log.Printf("loop [%d] deactivating created tenants and activating them again (x3) \n", l)

		// deactivate and activate back again (x3)
		for i := 0; i < 3; i++ {
			err := updateTentantWithRetry(client, classPizza, batchTenants.WithStatus(models.TenantActivityStatusCOLD))
			requireNil(err)
			err = updateTentantWithRetry(client, classPizza, batchTenants.WithStatus(models.TenantActivityStatusHOT))
			requireNil(err)

			err = updateTentantWithRetry(client, classSoup, batchTenants.WithStatus(models.TenantActivityStatusCOLD))
			requireNil(err)
			err = updateTentantWithRetry(client, classSoup, batchTenants.WithStatus(models.TenantActivityStatusHOT))
			requireNil(err)

			err = updateTentantWithRetry(client, classRisotto, batchTenants.WithStatus(models.TenantActivityStatusCOLD))
			requireNil(err)
			err = updateTentantWithRetry(client, classRisotto, batchTenants.WithStatus(models.TenantActivityStatusHOT))
			requireNil(err)

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

		gotTenantsSoup, err := client.Schema().TenantsGetter().
			WithClassName(classPizza).
			Do(ctx)
		requireNil(err)

		gotBatchTenantsSoup := Tenants(gotTenantsSoup).ByNames(batchTenants.Names()...)
		requireTrue(len(gotBatchTenantsSoup) == 30, "len(gotBatchTenantsSoup) == 30")
		requireTrue(gotBatchTenantsSoup.IsStatus(models.TenantActivityStatusHOT),
			"gotBatchTenantsSoup.IsStatus(models.TenantActivityStatusHOT)")

		gotTenantsRisotto, err := client.Schema().TenantsGetter().
			WithClassName(classRisotto).
			Do(ctx)
		requireNil(err)

		gotBatchTenantsRisotto := Tenants(gotTenantsRisotto).ByNames(batchTenants.Names()...)
		requireTrue(len(gotBatchTenantsRisotto) == 30, "len(gotBatchTenantsRisotto) == 30")
		requireTrue(gotBatchTenantsRisotto.IsStatus(models.TenantActivityStatusHOT),
			"gotBatchTenantsRisotto.IsStatus(models.TenantActivityStatusHOT)")

		r.Shuffle(len(allTenants), func(i, j int) {
			allTenants[i], allTenants[j] = allTenants[j], allTenants[i]
		})

		var (
			batchSize = 100
			i         = 0
			doTest    = func(batch Tenants, batchNum int) {
				log.Printf("loop [%d] batch [%d] activating 1st half of ALL tenants (act + 3x(deact + act))\n", l, batchNum)

				half := len(batch) / 2

				// effectively activate and  populate 1st half
				err = updateTentantWithRetry(client, classPizza, batch[:half].WithStatus(models.TenantActivityStatusHOT))
				requireNil(err)
				err = updateTentantWithRetry(client, classSoup, batch[:half].WithStatus(models.TenantActivityStatusHOT))
				requireNil(err)

				for i := 0; i < 3; i++ {
					err := updateTentantWithRetry(client, classPizza, batch[:half].WithStatus(models.TenantActivityStatusCOLD))
					requireNil(err)
					err = updateTentantWithRetry(client, classPizza, batch[:half].WithStatus(models.TenantActivityStatusHOT))
					requireNil(err)

					err = updateTentantWithRetry(client, classSoup, batch[:half].WithStatus(models.TenantActivityStatusCOLD))
					requireNil(err)
					err = updateTentantWithRetry(client, classSoup, batch[:half].WithStatus(models.TenantActivityStatusHOT))
					requireNil(err)

					log.Printf("loop [%d][%d] batch [%d] activated\n", l, i, batchNum)

				}

				// ==================================================================================

				log.Printf("loop [%d] batch [%d] populating 1st half of ALL tenants\n", l, batchNum)

				CreateDataPizzaForTenantsWithIds(client, getId, batch[:half].Names()...)
				CreateDataSoupForTenantsWithIds(client, getId, batch[:half].Names()...)
				CreateDataRisottoForTenantsWithIds(client, getId, batch[:half].Names()...)

				// ==================================================================================

				log.Printf("loop [%d] batch [%d] deactivating 2nd half of ALL tenants (3x(deact + act) + deact) \n", l, batchNum)

				// effectively deactivate 2nd half
				for i := 0; i < 3; i++ {
					err := updateTentantWithRetry(client, classPizza, batch[half:].WithStatus(models.TenantActivityStatusCOLD))
					requireNil(err)
					err = updateTentantWithRetry(client, classPizza, batch[half:].WithStatus(models.TenantActivityStatusHOT))
					requireNil(err)

					err = updateTentantWithRetry(client, classSoup, batch[half:].WithStatus(models.TenantActivityStatusCOLD))
					requireNil(err)
					err = updateTentantWithRetry(client, classSoup, batch[half:].WithStatus(models.TenantActivityStatusHOT))
					requireNil(err)

					log.Printf("loop [%d][%d] batch [%d] activated\n", l, i, batchNum)
				}

				err = updateTentantWithRetry(client, classPizza, batch[half:].WithStatus(models.TenantActivityStatusCOLD))
				requireNil(err)
				err = updateTentantWithRetry(client, classSoup, batch[half:].WithStatus(models.TenantActivityStatusCOLD))
				requireNil(err)
			}
		)

		fmt.Printf("len(allTenants): %d\n", len(allTenants))
		if len(allTenants) > batchSize {
			wg := sync.WaitGroup{}
			for ; i <= len(allTenants)-batchSize; i += batchSize {
				fmt.Printf("batching allTenants[%d:%d]\n", i, batchSize+i)
				i := i
				tenantsBatch := allTenants[i : batchSize+i]
				wg.Add(1)
				go func() {
					doTest(tenantsBatch, int(i/batchSize))
					wg.Done()
				}()
			}
			if len(allTenants) > i {
				fmt.Printf("batching allTenants[%d:%d]\n", i, len(allTenants))
				tenantsBatch := allTenants[i:]
				wg.Add(1)
				go func() {
					doTest(tenantsBatch, int(i/batchSize))
					wg.Done()
				}()
			}
			wg.Wait()
		} else {
			doTest(allTenants, 0)
		}
	}

	// ==================================================================================

	log.Println("TEST 2 finished. OK")
}

func updateTentantWithRetry(client *wvt.Client, className string, tenants Tenants) error {
	const maxRetries = 3
	var err error
	for attempt := 1; attempt <= maxRetries; attempt++ {
		err = client.Schema().TenantsUpdater().
			WithClassName(className).
			WithTenants(tenants...).
			Do(context.Background())
		if err == nil {
			return err
		}
		fmt.Printf("Attempt %d/%d failed: %v\n", attempt, maxRetries, getErrorWithDerivedError(err))

		if attempt == maxRetries {
			fmt.Println("Max retries reached. Aborting.")
			return err
		}

		time.Sleep(1 * time.Second)
	}
	return err
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
	const maxRetries = 3
	for attempt := 1; attempt <= maxRetries; attempt++ {
		err := isInactiveTenantObjects(client, className, tenantName)
		if err == nil {
			return
		}
		fmt.Printf("Attempt %d/%d failed: %v\n", attempt, maxRetries, getErrorWithDerivedError(err))

		if attempt == maxRetries {
			fmt.Println("Max retries reached. Aborting.")
			panic(err)
		}

		time.Sleep(2 * time.Second)
	}
}

func getErrorWithDerivedError(err error) error {
	var clientErr *fault.WeaviateClientError
	if errors.As(err, &clientErr) {
		return fmt.Errorf("%s: %w", clientErr.Error(), clientErr.DerivedFromError)
	}

	switch e := err.(type) {
	case *fault.WeaviateClientError:
		if e.DerivedFromError != nil {
			return fmt.Errorf("%s: %w", e.Error(), e.DerivedFromError)
		}
		return e
	default:
		return e
	}
}

func isInactiveTenantObjects(client *wvt.Client, className, tenantName string) error {
	objects, err := client.Data().ObjectsGetter().
		WithClassName(className).
		WithTenant(tenantName).
		Do(context.Background())
	if err == nil {
		return fmt.Errorf("expected error but got nil")
	}

	clientErr, ok := err.(*fault.WeaviateClientError)
	if !ok {
		return fmt.Errorf("unexpected error type: %v", err)
	}

	if clientErr.StatusCode != 422 {
		return fmt.Errorf("expected status code 422 but got %d", clientErr.StatusCode)
	}

	if !strings.Contains(clientErr.Msg, "tenant not active") {
		return fmt.Errorf("expected message to contain 'tenant not active' but got: %s", clientErr.Msg)
	}

	if objects != nil {
		return fmt.Errorf("expected objects to be nil but got: %v", objects)
	}

	return nil
}
