package main

import (
	"context"
	"fmt"
	"log"

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

	return

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

	classPizza := "Pizza"

	uuidCounter := uint32(0)
	getId := func() strfmt.UUID {
		uuidCounter++
		return strfmt.UUID(fmt.Sprintf("00000000-0000-0000-0000-%012x", uuidCounter-1))
	}

	name := func(id int) string {
		return fmt.Sprintf("tenant_%d", id)
	}

	ctx := context.Background()

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

	coldTenants := make(Tenants, 1)

	coldTenants[0] = models.Tenant{
		Name:           name(0),
		ActivityStatus: models.TenantActivityStatusCOLD,
	}

	// ==================================================================================

	log.Printf("creating inactive tenant\n")

	log.Printf("coldTenants: %v \n", coldTenants)

	CreateTenantsPizza(client, coldTenants...)

	// ==================================================================================

	log.Printf("activating tenants created as inactive\n")

	// activate created as inactive
	err = client.Schema().TenantsUpdater().
		WithClassName(classPizza).
		WithTenants(coldTenants.WithStatus(models.TenantActivityStatusHOT)...).
		Do(ctx)
	requireNil(err)

	// ==================================================================================

	log.Printf("populating tenants\n")

	CreateDataPizzaForTenantsWithIds(client, getId, coldTenants.Names()...)

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
