package alter_schema_operations

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

// Verification helpers shared by the drop-vector-index tests.
//
// As of Weaviate 1.39 dropping a vector index is a complete operation: besides
// removing the on-disk index, it (asynchronously) removes the named vector from
// every object and removes the vector's entry from the collection schema. These
// helpers assert that end-to-end contract.
//
// The removals are async, so the "after drop" assertions poll with require.Eventually.
// require.* must never be called inside an Eventually condition (a failed require
// calls runtime.Goexit and would abort the whole test), so the polling closures
// return false on error instead.

// vectorSampleSize is how many objects we sample when checking that a named
// vector is present/absent. A sample is enough to catch the behavior without
// fetching every object in the collection.
const vectorSampleSize = 10

// vectorCleanupTimeout bounds how long we wait for the async removal of a
// dropped vector from objects (active tenants / single-tenant collections),
// where cleanup completes quickly.
const vectorCleanupTimeout = 2 * time.Minute

// coldTenantVectorCleanupTimeout bounds how long we wait for the async removal
// of a dropped vector from a tenant that was INACTIVE during the drop and is
// then reactivated. Cleanup for a reactivated cold tenant is triggered on
// activation and runs through a rate-limited cleanup cycle, so it can take
// noticeably longer than the active-tenant case.
const coldTenantVectorCleanupTimeout = 5 * time.Minute

// schemaCleanupTimeout bounds how long we wait for a dropped vector to disappear
// from the collection schema. When removal happens it completes within a few
// seconds, so this is deliberately short: it keeps the final schema check quick
// even when a vector is (incorrectly) never removed.
const schemaCleanupTimeout = 45 * time.Second

// fetchSampleObjects fetches up to vectorSampleSize objects (with their named
// vectors) for the given class/tenant. tenant may be "" for single-tenant
// collections. It never fails the test, so it is safe to call from inside an
// Eventually condition.
func fetchSampleObjects(ctx context.Context, client *wvt.Client, class, tenant string) ([]*models.Object, error) {
	getter := client.Data().ObjectsGetter().
		WithClassName(class).
		WithVector().
		WithLimit(vectorSampleSize)
	if tenant != "" {
		getter = getter.WithTenant(tenant)
	}
	return getter.Do(ctx)
}

// objectHasNamedVector reports whether the object exposes a non-empty vector
// under the given name. The go-client decodes vectors as []float32 (regular) or
// [][]float32 (multi-vector); reflect keeps this type-agnostic.
func objectHasNamedVector(o *models.Object, vectorName string) bool {
	if o.Vectors == nil {
		return false
	}
	v, ok := o.Vectors[vectorName]
	if !ok || v == nil {
		return false
	}
	rv := reflect.ValueOf(v)
	if rv.Kind() == reflect.Slice || rv.Kind() == reflect.Array {
		return rv.Len() > 0
	}
	return true
}

// assertObjectsHaveNamedVector asserts that every sampled object exposes the
// named vector. Used before a drop to establish the vectors actually exist.
func assertObjectsHaveNamedVector(ctx context.Context, t *testing.T, client *wvt.Client, class, tenant, vectorName string) {
	t.Helper()
	require.Eventuallyf(t, func() bool {
		objs, err := fetchSampleObjects(ctx, client, class, tenant)
		if err != nil {
			t.Logf("fetch objects failed (class=%s tenant=%q): %v", class, tenant, err)
			return false
		}
		if len(objs) == 0 {
			return false
		}
		for _, o := range objs {
			if !objectHasNamedVector(o, vectorName) {
				return false
			}
		}
		return true
	}, 30*time.Second, time.Second,
		"expected all sampled objects to have named vector %q before drop (class=%s tenant=%q)", vectorName, class, tenant)
	t.Logf("verified sampled objects have vector %q (class=%s tenant=%q)", vectorName, class, tenant)
}

// assertObjectsLackNamedVector asserts that, after an async drop, no sampled
// object exposes the named vector anymore, using the default active-tenant
// cleanup timeout.
func assertObjectsLackNamedVector(ctx context.Context, t *testing.T, client *wvt.Client, class, tenant, vectorName string) {
	t.Helper()
	assertObjectsLackNamedVectorWithin(ctx, t, client, class, tenant, vectorName, vectorCleanupTimeout)
}

// assertObjectsLackNamedVectorWithin is like assertObjectsLackNamedVector but
// takes an explicit timeout, used for the reactivated cold-tenant case where
// cleanup is slower.
func assertObjectsLackNamedVectorWithin(ctx context.Context, t *testing.T, client *wvt.Client, class, tenant, vectorName string, timeout time.Duration) {
	t.Helper()
	require.Eventuallyf(t, func() bool {
		objs, err := fetchSampleObjects(ctx, client, class, tenant)
		if err != nil {
			t.Logf("fetch objects failed (class=%s tenant=%q): %v", class, tenant, err)
			return false
		}
		if len(objs) == 0 {
			return false
		}
		for _, o := range objs {
			if objectHasNamedVector(o, vectorName) {
				return false
			}
		}
		return true
	}, timeout, 2*time.Second,
		"expected sampled objects to no longer have named vector %q after drop (class=%s tenant=%q)", vectorName, class, tenant)
	t.Logf("verified sampled objects no longer have vector %q (class=%s tenant=%q)", vectorName, class, tenant)
}

// assertVectorDroppedFromSchema asserts that the named vector is removed from
// the collection's VectorConfig after the drop (async, class-level).
func assertVectorDroppedFromSchema(ctx context.Context, t *testing.T, client *wvt.Client, class, vectorName string) {
	t.Helper()
	require.Eventuallyf(t, func() bool {
		cls, err := client.Schema().ClassGetter().WithClassName(class).Do(ctx)
		if err != nil {
			t.Logf("get class %s failed: %v", class, err)
			return false
		}
		if cls == nil {
			return false
		}
		_, present := cls.VectorConfig[vectorName]
		return !present
	}, schemaCleanupTimeout, 2*time.Second,
		"expected named vector %q to be removed from %s schema after drop", vectorName, class)
	t.Logf("verified vector %q removed from %s schema", vectorName, class)
}
