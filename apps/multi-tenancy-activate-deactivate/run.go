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

const (
	host = "localhost:8080"
)

func main() {
	// test1()
	// test2()
	// test3()
	test4()
}

func test1() {
	log.Println("TEST 1 starting")

	classPizza := "Pizza"
	classSoup := "Soup"
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: host})
	requireNil(err)

	cleanup := func() {
		err := client.Schema().AllDeleter().Do(context.Background())
		requireNil(err)
	}
	cleanup()
	// defer cleanup()

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

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: host})
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

		// fmt.Println(classPizza)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		// activate created as inactive
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classSoup)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classRisotto)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classRisotto).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)

		// ==================================================================================

		log.Printf("loop [%d] deactivating created tenants and activating them again (x3) \n", l)

		// deactivate and activate back again (x3)
		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classRisotto)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classRisotto).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classRisotto)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classRisotto).
				Do(ctx)
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

		half := len(allTenants) / 2
		r.Shuffle(len(allTenants), func(i, j int) {
			allTenants[i], allTenants[j] = allTenants[j], allTenants[i]
		})

		// ==================================================================================

		log.Printf("loop [%d] activating 1st half of ALL tenants (act + 3x(deact + act))\n", l)

		// effectively activate and  populate 1st half
		// fmt.Println(classPizza)
		// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classSoup)
		// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)

		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			fmt.Println(classSoup)
			fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// ==================================================================================

		log.Printf("loop [%d] populating 1st half of ALL tenants\n", l)

		CreateDataPizzaForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		CreateDataSoupForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		CreateDataRisottoForTenantsWithIds(client, getId, allTenants[:half].Names()...)

		// ==================================================================================

		log.Printf("loop [%d] deactivating 2nd half of ALL tenants (3x(deact + act) + deact) \n", l)

		// effectively deactivate 2nd half
		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// fmt.Println(classPizza)
		// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			Do(ctx)
		requireNil(err)

		// fmt.Println(classSoup)
		// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			Do(ctx)
		requireNil(err)

		log.Printf("loop [%d/%d] finished\n", l, loops)
	}

	log.Println("TEST 2 finished. OK")
}

func test3WIP() {
	log.Println("TEST 3 starting")

	loops := 50
	tenantCount := 1000
	internalCycleCount := 10
	batchSize := 100
	classPizza := "Pizza"

	uuidCounter := uint32(0)
	getId := func() strfmt.UUID {
		uuidCounter++
		return strfmt.UUID(fmt.Sprintf("00000000-0000-0000-0000-%012x", uuidCounter-1))
	}
	nextTenantId := 0
	name := func(id int) string {
		return fmt.Sprintf("tenant_%d", id)
	}

	tenants := make(Tenants, tenantCount)
	for i := 0; i < tenantCount; i++ {
		nextTenantId++
		tenants[i] = models.Tenant{
			Name:           name(nextTenantId),
			ActivityStatus: models.TenantActivityStatusHOT,
		}
	}

	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: host})
	requireNil(err)

	cleanup := func() {
		err := client.Schema().AllDeleter().Do(context.Background())
		requireNil(err)
	}
	cleanup()
	// defer cleanup()

	// ==================================================================================

	log.Println("creating MT classes")

	CreateSchemaPizzaForTenants(client)

	for l := 1; l <= loops; l++ {
		log.Printf("loop [%d/%d] started\n", l, loops)

		coldTenants := make(Tenants, tenantCount)
		hotTenants := make(Tenants, tenantCount)

		hotColdTenants := make(Tenants, internalCycleCount*tenantCount)

		for i := 0; i < tenantCount; i++ {
			coldTenants[i] = models.Tenant{
				Name:           tenants[i].Name,
				ActivityStatus: models.TenantActivityStatusCOLD,
			}
			hotTenants[i] = models.Tenant{
				Name:           tenants[i].Name,
				ActivityStatus: models.TenantActivityStatusHOT,
			}
		}
		for j := 0; j < internalCycleCount; j++ {
			for i := 0; i < tenantCount; i++ {
				if i%2 == 0 {
					hotColdTenants[j*tenantCount+i] = models.Tenant{
						Name:           tenants[i].Name,
						ActivityStatus: models.TenantActivityStatusHOT,
					}
				} else {
					hotColdTenants[j*tenantCount+i] = models.Tenant{
						Name:           tenants[i].Name,
						ActivityStatus: models.TenantActivityStatusCOLD,
					}
				}
			}
		}

		log.Printf("loop [%d] creating tenants and populating 1/3 of them\n", l)

		CreateTenantsPizza(client, hotTenants...)

		for j := 0; j < internalCycleCount; j++ {
			for i := 0; i < tenantCount; i++ {
				if i%2 == 0 {
					err = client.Schema().TenantsUpdater().
						WithClassName(classPizza).
						WithTenants(coldTenants...).
						Do(ctx)
					requireNil(err)

				} else {
					err = client.Schema().TenantsUpdater().
						WithClassName(classPizza).
						WithTenants(hotTenants...).
						Do(ctx)
					requireNil(err)

				}
			}

			err = client.Schema().TenantsUpdater().
				WithClassName(classPizza).
				WithTenants(hotTenants...).
				Do(ctx)
			requireNil(err)

			objects := make([]*models.Object, batchSize)
			for i := 0; i < batchSize; i++ {
				objects[i] = &models.Object{
					Class:  classPizza,
					ID:     getId(),
					Tenant: tenants[0].Name,
				}
			}

			createData(client, objects)

		}
		log.Printf("loop [%d/%d] finished\n", l, loops)
	}

	log.Println("TEST 3 finished. OK")
}

func test4() {
	log.Println("TEST 4 starting")

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

	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: host})
	requireNil(err)

	cleanup := func() {
		err := client.Schema().AllDeleter().Do(context.Background())
		requireNil(err)
	}
	cleanup()
	// defer cleanup()

	// ==================================================================================

	log.Println("creating MT classes")

	CreateSchemaPizzaForTenants(client)
	CreateSchemaSoupForTenants(client)
	CreateSchemaRisottoForTenants(client)

	coldBuf := make(Tenants, 10)
	hotBuf := make(Tenants, 10)
	hotWithDataBuf := make(Tenants, 10)
	var allTenants Tenants

	for l := 1; l <= 12; l++ {
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
	}

	for l := 13; l <= loops; l++ {
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

		// fmt.Println(classPizza)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		// activate created as inactive
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classSoup)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classRisotto)
		// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classRisotto).
			WithTenants(coldBuf.WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)

		// ==================================================================================

		log.Printf("loop [%d] deactivating created tenants and activating them again (x3) \n", l)

		// deactivate and activate back again (x3)
		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classRisotto)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classRisotto).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classRisotto)
			// fmt.Println(coldBuf.WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(batchTenants.WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classRisotto).
				Do(ctx)
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

		half := len(allTenants) / 2
		r.Shuffle(len(allTenants), func(i, j int) {
			allTenants[i], allTenants[j] = allTenants[j], allTenants[i]
		})

		// ==================================================================================

		log.Printf("loop [%d] activating 1st half of ALL tenants (act + 3x(deact + act))\n", l)

		// effectively activate and  populate 1st half
		// fmt.Println(classPizza)
		// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)
		// fmt.Println(classSoup)
		// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
			Do(ctx)
		requireNil(err)

		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[:half].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[:half].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// ==================================================================================

		log.Printf("loop [%d] populating 1st half of ALL tenants\n", l)

		CreateDataPizzaForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		CreateDataSoupForTenantsWithIds(client, getId, allTenants[:half].Names()...)
		CreateDataRisottoForTenantsWithIds(client, getId, allTenants[:half].Names()...)

		// ==================================================================================

		log.Printf("loop [%d] deactivating 2nd half of ALL tenants (3x(deact + act) + deact) \n", l)

		// effectively deactivate 2nd half
		for i := 0; i < 3; i++ {
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
			err := client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classPizza)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classPizza).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)
			// fmt.Println(classSoup)
			// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusHOT))
			err = client.Schema().TenantsUpdater().
				WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusHOT)...).
				WithClassName(classSoup).
				Do(ctx)
			requireNil(err)

			log.Printf("loop [%d][%d] activated\n", l, i)
		}

		// fmt.Println(classPizza)
		// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
		err = client.Schema().TenantsUpdater().
			WithClassName(classPizza).
			WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			Do(ctx)
		requireNil(err)

		// fmt.Println(classSoup)
		// fmt.Println(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD))
		err = client.Schema().TenantsUpdater().
			WithClassName(classSoup).
			WithTenants(allTenants[half:].WithStatus(models.TenantActivityStatusCOLD)...).
			Do(ctx)
		requireNil(err)

		log.Printf("loop [%d/%d] finished\n", l, loops)
	}

	log.Println("TEST 4 finished. OK")
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
