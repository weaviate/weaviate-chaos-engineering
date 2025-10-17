package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/go-openapi/strfmt"
	"github.com/google/uuid"
	"github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/fault"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
	"golang.org/x/sync/errgroup"
)

func main() {
	if err := do(context.Background()); err != nil {
		log.Fatal(getErrorWithDerivedError(err))
	}
}

func do(ctx context.Context) error {
	origin, err := getStringVar("ORIGIN")
	if err != nil {
		return err
	}

	client, err := newClient(origin)
	if err != nil {
		return err
	}

	if err := client.Schema().AllDeleter().Do(ctx); err != nil {
		return err
	}

	if err := client.Schema().ClassCreator().WithClass(getClass()).Do(ctx); err != nil {
		return err
	}

	if err := importAndCheck(ctx, client); err != nil {
		return err
	}

	return nil
}

const (
	className = "CompactionAndCleanupClass"
	propName  = "heavy_payload"
	charset   = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

// importAndCheck imports relatively small batches with huge payloads. The
// purpose of the huge payloads is to lead to very frequent compactions and later
// cleanups. The goal of this test is to make sure that bloom filters, count net
// additions are always correct and proper objects are returned after compactions
// and cleanups.
//
// In addition, after every couple of batches, a certain percentage of objects
// is deleted and other percentage is updated. The deletes/updates could be an object
// that was just written (a memtable delete/update) or it could be an object that
// was written a long time ago (a "segment delete/update" - Note there is no such thing,
// any new write lands in a memtable first. But what is meant is that the delete/update
// affects an object that has already been flushed. Thus the operation spans more than
// a single segment).
//
// By following a fixed schedule of importing, deleting and updating after each cycle
// we know how many objects there should be. Part of this test is asserting that
// the Aggregate { meta { count } } response matches this number.
func importAndCheck(ctx context.Context, client *weaviate.Client) error {
	cycles := 100
	batchesPerCycle := 20
	objectsPerBatch := 100
	payloadSize := 5 * 1024
	deletesPerCycle := 300
	updatesPerCycle := 200
	checksPerCycle := 100
	netAdditionsPerCycle := batchesPerCycle*objectsPerBatch - deletesPerCycle
	keepChecking := 5 * time.Minute

	objTracker := newObjectTracker(payloadSize, 10)

	beforeCycles := time.Now()
	expectedCount := 0
	for cycle := range cycles {
		log.Printf("starting cycle %d", cycle)

		for batch := range batchesPerCycle {
			if err := createObjects(ctx, client, objTracker, objectsPerBatch); err != nil {
				return err
			}
			log.Printf("finished batch %d", (cycle*batchesPerCycle)+batch)
		}

		if err := deleteObjects(ctx, client, objTracker, deletesPerCycle); err != nil {
			return err
		}
		log.Printf("deleted %d objects", deletesPerCycle)

		if err := updateObjects(ctx, client, objTracker, updatesPerCycle); err != nil {
			return err
		}
		log.Printf("updated %d objects", updatesPerCycle)

		expectedCount = netAdditionsPerCycle * (cycle + 1)
		log.Printf("there should now be %d objects left", expectedCount)
		if err := assertCount(ctx, client, expectedCount); err != nil {
			return err
		}
		log.Printf("count check passed!")

		log.Printf("checking %d random objects", checksPerCycle)
		if err := assertObjects(ctx, client, objTracker, checksPerCycle); err != nil {
			return err
		}
		log.Printf("object check passed!")
	}

	log.Printf("import and check cycle took %s", time.Since(beforeCycles))
	log.Printf("more compactions and cleanups will be happening in the background, keep checking for %s", keepChecking)
	if err := keepCheckingAfterCompletion(ctx, client, objTracker, keepChecking, expectedCount, checksPerCycle); err != nil {
		return err
	}
	log.Printf("passed!")

	return nil
}

func keepCheckingAfterCompletion(ctx context.Context, client *weaviate.Client,
	idTracker *objectTracker, keepChecking time.Duration, desiredCount int,
	checksPerCycle int,
) error {
	ctx, cancel := context.WithTimeout(ctx, keepChecking)
	defer cancel()

	t := time.NewTicker(777 * time.Millisecond)
	defer t.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
			if err := assertCount(ctx, client, desiredCount); err != nil {
				return err
			}
			if err := assertObjects(ctx, client, idTracker, checksPerCycle); err != nil {
				return err
			}
			log.Printf("passed count + objects check")
		}
	}
}

func createObjects(ctx context.Context,
	client *weaviate.Client, objTracker *objectTracker, count int,
) error {
	id2Payload := objTracker.CreateIDs(count)
	objects := make([]*models.Object, count)

	i := 0
	for id, payload := range id2Payload {
		objects[i] = &models.Object{
			Class:      className,
			ID:         id,
			Properties: map[string]any{propName: payload},
		}
		i++
	}

	resps, err := client.Batch().ObjectsBatcher().WithObjects(objects...).Do(ctx)
	if err != nil {
		return fmt.Errorf("create objects: %w", err)
	}

	for _, resp := range resps {
		if resp.Result.Errors != nil && len(resp.Result.Errors.Error) > 0 {
			return fmt.Errorf("create objects: %s", resp.Result.Errors.Error[0].Message)
		}
	}
	return nil
}

func deleteObjects(ctx context.Context,
	client *weaviate.Client, objTracker *objectTracker, count int,
) error {
	ids, err := objTracker.DeleteIDs(count)
	if err != nil {
		return fmt.Errorf("delete objects: %w", err)
	}

	deleter := client.Data().Deleter().WithClassName(className)
	for _, id := range ids {
		err := deleter.WithID(string(id)).Do(ctx)
		if err != nil {
			return fmt.Errorf("delete objects %q: %w", id, err)
		}
	}
	return nil
}

func updateObjects(ctx context.Context,
	client *weaviate.Client, objTracker *objectTracker, count int,
) error {
	id2Payload, err := objTracker.UpdateIDs(count)
	if err != nil {
		return fmt.Errorf("update objects: %w", err)
	}

	updater := client.Data().Updater().WithClassName(className)
	for id, payload := range id2Payload {
		err := updater.WithID(string(id)).WithProperties(map[string]any{propName: payload}).Do(ctx)
		if err != nil {
			return fmt.Errorf("update objects %q: %w", id, err)
		}
	}
	return nil
}

func assertCount(ctx context.Context, client *weaviate.Client, expectedCount int) error {
	res, err := client.GraphQL().Aggregate().WithClassName(className).
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	if err != nil {
		return err
	}

	count := int(res.Data["Aggregate"].(map[string]any)[className].([]any)[0].(map[string]any)["meta"].(map[string]any)["count"].(float64))

	if expectedCount != count {
		return fmt.Errorf("wanted %d, got %d", expectedCount, count)
	}

	return nil
}

func assertObjects(ctx context.Context,
	client *weaviate.Client, objTracker *objectTracker, count int,
) error {
	id2Prefix, err := objTracker.CheckIDs(count)
	if err != nil {
		return fmt.Errorf("assert objects: %w", err)
	}

	eg := &errgroup.Group{}
	for id, prefix := range id2Prefix {
		id, prefix := id, prefix
		eg.Go(func() error {
			res, err := client.Data().ObjectsGetter().WithClassName(className).WithID(string(id)).Do(ctx)
			if err != nil {
				return err
			}

			if len(res) != 1 {
				return fmt.Errorf("expected len 1, got %d", len(res))
			}

			obj := res[0]
			if obj.ID != id {
				return fmt.Errorf("id mismatch %q vs %q", obj.ID, id)
			}
			if payload, ok := obj.Properties.(map[string]any)[propName]; !ok {
				return fmt.Errorf("missing property in %q", id)
			} else if strPayload := payload.(string); !strings.HasPrefix(strPayload, prefix) {
				return fmt.Errorf("property missmatch in %q: %q vs %q", id, strPayload[len(prefix)], prefix)
			}

			return nil
		})
	}

	if err := eg.Wait(); err != nil {
		return fmt.Errorf("assert objects: %w", err)
	}
	return nil
}

type objectTracker struct {
	store       map[strfmt.UUID]string
	payloadSize int
	prefixSize  int
	rand        *rand.Rand
}

func newObjectTracker(payloadSize, prefixSize int) *objectTracker {
	return &objectTracker{
		store:       map[strfmt.UUID]string{},
		payloadSize: payloadSize,
		prefixSize:  prefixSize,
		rand:        rand.New(rand.NewSource(time.Now().UnixNano())),
	}
}

func (ot *objectTracker) CreateIDs(count int) map[strfmt.UUID]string {
	out := make(map[strfmt.UUID]string, count)
	for range count {
		id := strfmt.UUID(uuid.New().String())
		payload := ot.genPayload()

		out[id] = payload
		ot.store[id] = payload[:ot.prefixSize]
		// fmt.Printf(". ==> id [%s]\n.     store [%s]\n.     out [%s]\n\n", id, out[id], ot.store[id])
	}
	return out
}

func (ot *objectTracker) DeleteIDs(count int) ([]strfmt.UUID, error) {
	ids, err := ot.randomIDs(count)
	if err != nil {
		return nil, err
	}

	for _, id := range ids {
		delete(ot.store, id)
	}
	return ids, nil
}

func (ot *objectTracker) UpdateIDs(count int) (map[strfmt.UUID]string, error) {
	ids, err := ot.randomIDs(count)
	if err != nil {
		return nil, err
	}

	out := make(map[strfmt.UUID]string, count)
	for _, id := range ids {
		payload := ot.genPayload()

		out[id] = payload
		ot.store[id] = payload[:ot.prefixSize]
	}
	return out, nil
}

func (ot *objectTracker) CheckIDs(count int) (map[strfmt.UUID]string, error) {
	ids, err := ot.randomIDs(count)
	if err != nil {
		return nil, err
	}

	out := make(map[strfmt.UUID]string, count)
	for _, id := range ids {
		out[id] = ot.store[id]
	}
	return out, nil
}

// randomIDs returns n random ids without altering them in the tracker
func (ot *objectTracker) randomIDs(count int) ([]strfmt.UUID, error) {
	if count > len(ot.store) {
		return nil, fmt.Errorf("can't retrieve more ids than there are in the store: %d vs %d",
			len(ot.store), count)
	}

	allIDs := make([]strfmt.UUID, len(ot.store))
	i := 0
	for id := range ot.store {
		allIDs[i] = id
		i++
	}
	ot.rand.Shuffle(len(allIDs), func(i, j int) { allIDs[i], allIDs[j] = allIDs[j], allIDs[i] })

	return allIDs[:count], nil
}

func (ot *objectTracker) genPayload() string {
	buf := make([]byte, ot.payloadSize)
	for i := range buf {
		buf[i] = charset[ot.rand.Intn(len(charset))]
	}
	return string(buf)
}

func newClient(origin string) (*weaviate.Client, error) {
	parsed, err := url.Parse(origin)
	if err != nil {
		return nil, err
	}

	cfg := weaviate.Config{
		Host:             parsed.Host,
		Scheme:           parsed.Scheme,
		ConnectionClient: &http.Client{Timeout: 2 * time.Minute},
	}
	return weaviate.New(cfg), nil
}

func getStringVar(envName string) (string, error) {
	v := os.Getenv(envName)
	if v == "" {
		return v, fmt.Errorf("missing required variable %s", envName)
	}

	return v, nil
}

func getClass() *models.Class {
	vFalse := false
	return &models.Class{
		Class:           className,
		Vectorizer:      "none",
		VectorIndexType: "hnsw",
		VectorIndexConfig: map[string]interface{}{
			"skip": true,
		},
		Properties: []*models.Property{
			{
				Name:            propName,
				DataType:        []string{"string"},
				IndexSearchable: &vFalse,
				IndexFilterable: &vFalse,
			},
		},
	}
}

func getErrorWithDerivedError(err error) error {
	switch e := err.(type) {
	case *fault.WeaviateClientError:
		if e.DerivedFromError != nil {
			return fmt.Errorf("%s: %w", e.Error(), getErrorWithDerivedError(e.DerivedFromError))
		}
	default:
	}
	return err
}
