## How to reproduce

1. Start a 3-node RAFT cluster locally. It should use the ports 8080, 8081, and
   8082 for http, and 50051, 50052, and 50053 for gRPC. If you use different
   ports, adjust the scripts accordingly.

2. Run `python3 run.py import-phase-1` to import the data. This will take 5-10
   minutes.

3. Alter weaviate by putting a `fmt.Println(err)` and `os.Exit(1)` in the
   deferred function in `shard.New()`. This way the node will crash as soon as
   we see the error.

3. Now introduce races by concurrently running 3 things:
   - `python3 tenants_on_off.py` which sends activation/deactivation requests
     to the server.
   - `python3 run.py query_bm25.py` to send implicit activation and hybrid queries
   - Then restart individual nodes. If there is no race within the first 60s or
     (or however long it takes to lazy-load all shards), you're likely not
     going to see the issue. Try again, by restarting a node. Make sure the
     other two scripts keep running.
