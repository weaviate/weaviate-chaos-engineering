package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/go-openapi/strfmt"
	"github.com/google/uuid"
	"github.com/semi-technologies/weaviate-go-client/v4/weaviate"
	"github.com/semi-technologies/weaviate-go-client/v4/weaviate/graphql"
	"github.com/semi-technologies/weaviate/entities/models"
	"golang.org/x/sync/errgroup"
)

func main() {
	if err := do(context.Background()); err != nil {
		log.Fatal(err)
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

// importAndCheck imports relatively small batches with huge payloads. The
// purpose of the huge payloads is to lead to very frequent compactions. Those
// compactions generate the noise that this tests needs: The goal of this test
// is to make sure that bloom filters and count net additions are always
// correct, even if frequent compactions.
//
// in addition, after every couple of batches, a certain percentage of objects
// is deleted. The deletes could be an object that was just written (a memtable
// delete) or it could be an object that was written a long time ago (a
// "segment delete" - Note there is no such thing, any new write lands in a
// memtable first. But what is meant is that the delete affects an object that
// has already been flushed. Thus the operation spans more than a single
// segment).
//
// By following a fixed schedule of importing and deleting after each cycle we
// know how many objects there should be. Part of this test is asserting that
// the Aggregate { meta { count } } response matches this number.
func importAndCheck(ctx context.Context, client *weaviate.Client) error {
	cycles := 400
	batchesPerCycle := 10
	objectsPerBatch := 100
	payloadSize := 5 * 1024
	deletesPerCycle := 100
	checksPerCycle := 100
	netAdditionsPerCycle := batchesPerCycle*objectsPerBatch - deletesPerCycle
	keepChecking := 5 * time.Minute

	ids := newIdTracker()

	beforeCycles := time.Now()
	desiredCount := 0
	for cycle := 0; cycle < cycles; cycle++ {
		log.Printf("starting cycle %d", cycle)

		for batch := 0; batch < batchesPerCycle; batch++ {
			batcher := client.Batch().ObjectsBatcher()
			res, err := batcher.WithObjects(genObjects(ids, objectsPerBatch, payloadSize)...).
				Do(ctx)
			if err != nil {
				return err
			}

			for _, obj := range res {
				if obj.Result.Errors != nil && len(obj.Result.Errors.Error) > 0 {
					return fmt.Errorf(obj.Result.Errors.Error[0].Message)
				}
			}
			log.Printf("finished batch %d", (cycle*batchesPerCycle)+batch)
		}

		if err := deleteObjects(ctx, client, ids, deletesPerCycle); err != nil {
			return err
		}
		log.Printf("deleted %d objects", deletesPerCycle)

		desiredCount = netAdditionsPerCycle * (cycle + 1)
		log.Printf("there should now be %d objects left", desiredCount)
		if err := assertCount(ctx, client, desiredCount); err != nil {
			return err
		}
		log.Printf("count check passed!")

		log.Printf("checking %d random objects", checksPerCycle)
		if err := assertObjects(ctx, client, ids, checksPerCycle); err != nil {
			return err
		}
		log.Printf("object check passed!")
	}

	log.Printf("import and check cycle took %s", time.Since(beforeCycles))
	log.Printf("more compactions will be happening in the background, keep checking for %s", keepChecking)
	if err := keepCheckingAfterCompletion(ctx, client, ids, keepChecking, desiredCount, checksPerCycle); err != nil {
		return err
	}
	log.Printf("passed!")

	return nil
}

func keepCheckingAfterCompletion(ctx context.Context, client *weaviate.Client,
	idTracker *idTracker, keepChecking time.Duration, desiredCount int,
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

func genObjects(idTracker *idTracker, objectsPerBatch, payloadSize int) []*models.Object {
	ids := idTracker.CreateIDs(objectsPerBatch)
	out := make([]*models.Object, len(ids))
	for i, id := range ids {
		out[i] = &models.Object{
			Class: "ClassNoVectorIndex",
			ID:    id,
			Properties: map[string]interface{}{
				"heavy_payload": genPayload(payloadSize),
			},
		}
	}

	return out
}

func genPayload(size int) string {
	out := make([]byte, size)
	rand.Read(out)
	return string(out)
}

func deleteObjects(ctx context.Context,
	client *weaviate.Client, idTracker *idTracker, count int,
) error {
	ids, err := idTracker.DeleteIDs(count)
	if err != nil {
		return err
	}

	for _, id := range ids {
		err = client.Data().Deleter().
			WithClassName("ClassNoVectorIndex").WithID(string(id)).Do(ctx)
		if err != nil {
			return err
		}
	}

	return nil
}

func assertCount(ctx context.Context, client *weaviate.Client, desiredCount int) error {
	res, err := client.GraphQL().Aggregate().WithClassName("ClassNoVectorIndex").
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	if err != nil {
		return err
	}

	actual := int(res.Data["Aggregate"].(map[string]interface{})["ClassNoVectorIndex"].([]interface{})[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64))

	if desiredCount != actual {
		return fmt.Errorf("wanted %d, got %d", desiredCount, actual)
	}

	return nil
}

func assertObjects(ctx context.Context, client *weaviate.Client,
	idTracker *idTracker, count int,
) error {
	ids, err := idTracker.RandomIDs(count)
	if err != nil {
		return err
	}

	eg := &errgroup.Group{}
	for _, id := range ids {
		id := id
		eg.Go(func() error {
			res, err := client.Data().ObjectsGetter().WithClassName("ClassNoVectorIndex").
				WithID(string(id)).Do(ctx)
			if err != nil {
				return err
			}

			if len(res) != 1 {
				return fmt.Errorf("expected len 1, got %d", len(res))
			}

			obj := res[0]
			if obj.ID != id {
				return fmt.Errorf("id mismatch")
			}

			return nil
		})
	}

	return eg.Wait()
}

type idTracker struct {
	store map[strfmt.UUID]struct{}
}

func newIdTracker() *idTracker {
	return &idTracker{
		store: map[strfmt.UUID]struct{}{},
	}
}

// CreateIDs creates n ids and tracks them internally
func (idt *idTracker) CreateIDs(batchSize int) []strfmt.UUID {
	out := make([]strfmt.UUID, batchSize)
	for i := range out {
		id := strfmt.UUID(uuid.New().String())
		out[i] = id
		idt.store[id] = struct{}{}
	}

	return out
}

// DeleteIDs returns n random ids from the store and deletes them
func (idt *idTracker) DeleteIDs(batchSize int) ([]strfmt.UUID, error) {
	if batchSize > len(idt.store) {
		return nil, fmt.Errorf("can't delete more ids than there are in the store: %d vs %d",
			len(idt.store), batchSize)
	}

	allIDs := make([]strfmt.UUID, len(idt.store))
	i := 0
	for id := range idt.store {
		allIDs[i] = id
		i++
	}

	rand.Seed(time.Now().UnixNano())
	rand.Shuffle(len(allIDs), func(i, j int) { allIDs[i], allIDs[j] = allIDs[j], allIDs[i] })

	out := allIDs[:batchSize]

	for _, id := range out {
		delete(idt.store, id)
	}

	return out, nil
}

// RandomIDs returns n random ids without altering them in the tracker
func (idt *idTracker) RandomIDs(batchSize int) ([]strfmt.UUID, error) {
	if batchSize > len(idt.store) {
		return nil, fmt.Errorf("can't retrieve more ids than there are in the store: %d vs %d",
			len(idt.store), batchSize)
	}

	allIDs := make([]strfmt.UUID, len(idt.store))
	i := 0
	for id := range idt.store {
		allIDs[i] = id
		i++
	}

	rand.Seed(time.Now().UnixNano())
	rand.Shuffle(len(allIDs), func(i, j int) { allIDs[i], allIDs[j] = allIDs[j], allIDs[i] })

	out := allIDs[:batchSize]

	return out, nil
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
	return &models.Class{
		Class:           "ClassNoVectorIndex",
		Vectorizer:      "none",
		VectorIndexType: "hnsw",
		VectorIndexConfig: map[string]interface{}{
			"skip": true,
		},
		Properties: []*models.Property{
			{
				Name:     "heavy_payload",
				DataType: []string{"string"},
			},
		},
	}
}
