package main

import (
	"bytes"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/pkg/errors"

	_ "net/http/pprof"
)

func main() {
	go func() {
		log.Println(http.ListenAndServe("localhost:5050", nil))
	}()

	if err := do(); err != nil {
		log.Fatal(err)
	}
}

func do() error {
	dims, err := getIntVar("DIMENSIONS")
	if err != nil {
		return err
	}

	size, err := getIntVar("SIZE")
	if err != nil {
		return err
	}

	batchSize, err := getIntVar("BATCH_SIZE")
	if err != nil {
		return err
	}

	origin, err := getStringVar("ORIGIN")
	if err != nil {
		return err
	}

	httpClient := &http.Client{}

	count := 0
	beforeAll := time.Now()
	// Now perform delete and writes in batches
	for i := 0; i < 3; i++ {
		count = 0
		for count < size {
			batcher := newDeleteBatch()
			batcher.deleteObject(fmt.Sprintf(`%d`, count+1))
			before := time.Now()
			if err := batcher.send(httpClient, origin, "DELETE"); err != nil {
				return err
			}
			fmt.Printf("%f%% complete deletion batch - last batch took %s - total %s\n",
				float32(count)/float32(size)*100,
				time.Since(before), time.Since(beforeAll))

			batcher = newBatch()
			for i := 0; i < batchSize; i++ {
				props := fmt.Sprintf(`{"itemId":%d}`, count+1)
				batcher.addObject(props, randomVector(dims))
			}

			before = time.Now()
			if err := batcher.send(httpClient, origin, "POST"); err != nil {
				return err
			}
			fmt.Printf("%f%% complete write batch - last batch took %s - total %s\n",
				float32(count)/float32(size)*100,
				time.Since(before), time.Since(beforeAll))

			count += batchSize
		}
	}

	return nil
}

type batch struct {
	bytes.Buffer
	hasElements bool
}

func newBatch() *batch {
	b := &batch{}

	b.WriteString(`{"objects":[`)
	return b
}

func newDeleteBatch() *batch {
	b := &batch{}

	b.WriteString(`{"match":`)
	return b
}

func (b *batch) deleteObject(propsString string) {
	if b.hasElements {
		b.WriteString(",")
	}
	deleteString := fmt.Sprintf(`{"class":"DemoClass","where":{"operator":"Equal","path":["itemId"],"valueInt":%s}},"output": "minimal","deletionTimeUnixMilli": 1,"dryRun":false}`, propsString)
	b.WriteString(deleteString)
	b.hasElements = true
}

func (b *batch) addObject(propsString string, vec []float32) {
	if b.hasElements {
		b.WriteString(",")
	}
	b.WriteString(fmt.Sprintf(`{"class":"DemoClass","properties":%s, "vector":[`, propsString))
	for i, dim := range vec {
		if i != 0 {
			b.WriteString(",")
		}
		b.WriteString(fmt.Sprintf("%f", dim))
	}
	b.WriteString("]}")
	b.hasElements = true
}

func (b *batch) send(client *http.Client, origin string, method string) error {

	if method == "POST" {
		b.WriteString("]}")
	}

	body := b.Bytes()
	r := bytes.NewReader(body)

	req, err := http.NewRequest(method, origin+"/v1/batch/objects", r)
	if err != nil {
		return err
	}

	req.Header.Add("content-type", "application/json")

	const maxRetries = 100
	const retryDelay = 1 * time.Second

	var res *http.Response

	for attempt := 0; attempt <= maxRetries; attempt++ {
		res, err = client.Do(req)

		if err == nil && res != nil && res.StatusCode == 200 {
			io.ReadAll(res.Body)
			res.Body.Close()
			return nil
		}

		if attempt < maxRetries {
			fmt.Printf("Attempt %d failed (error: %v). Retrying in 1s...\n", attempt, err)
			time.Sleep(retryDelay)

			r.Seek(0, 0)
		} else {
			fmt.Printf("Aborting after %d retries\n", maxRetries)
		}
	}

	if err != nil {
		return fmt.Errorf("request failed after %d retries: %v", maxRetries, err)
	}

	msg, _ := io.ReadAll(res.Body)
	res.Body.Close()
	return errors.Errorf("status %d: %s", res.StatusCode, string(msg))

}

func randomVector(dim int) []float32 {
	out := make([]float32, dim)
	for i := range out {
		out[i] = rand.Float32()
	}
	return out
}

func getIntVar(envName string) (int, error) {
	v := os.Getenv(envName)
	if v == "" {
		return 0, errors.Errorf("missing required variable %s", envName)
	}

	asInt, err := strconv.Atoi(v)
	if err != nil {
		return 0, err
	}

	return asInt, nil
}

func getStringVar(envName string) (string, error) {
	v := os.Getenv(envName)
	if v == "" {
		return v, errors.Errorf("missing required variable %s", envName)
	}

	return v, nil
}
