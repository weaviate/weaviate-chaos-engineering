import weaviate, { WeaviateClient, Collection } from "weaviate-client";

type Args = {
  client: WeaviateClient;
  collection: Collection<any, any, any>;
};

const HOW_MANY = 100000;
const COLLECTION_NAME = "ServerSideBatchingWithRollingRestart";

const setup = async (client: WeaviateClient) =>
  client.collections
    .create({
      name: COLLECTION_NAME,
      properties: [
        { name: "title", dataType: "text" },
        { name: "content", dataType: "text" },
      ],
      replication: weaviate.configure.replication({
        factor: 3,
        asyncEnabled: true,
      }),
      vectorizers: weaviate.configure.vectorizer.selfProvided(),
    })
    .then((collection) => ({ collection, client }));

const import_ = async (args: Args) => {
  const batching = await args.client.batch.stream();
  for (let i = 0; i < HOW_MANY; i++) {
    await batching.addObject({
      collection: args.collection.name,
      properties: {
        title: `Document ${i}`,
        content: `This is the content of document ${i}.`,
      },
      vectors: new Array(128).fill(0).map(() => Math.random()),
    });
    if (i % 10000 === 0) {
      console.log(
        `Imported ${await args.collection.length()} after processing ${i} objects...`,
      );
    }
  }
  await batching.stop();
  const errs = Object.values(batching.objErrors());
  errs.forEach(console.error);
  if (errs.length > 0) {
    throw new Error(`Encountered ${errs.length} errors during batch import`);
  }
  return args;
};

const verify = async (args: Args) => {
  let actual = 0;
  let count = 0;
  while (actual < HOW_MANY) {
    actual = await args.collection.length();
    console.log(
      `Found ${actual} objects so far, waiting for async replication to reach ${HOW_MANY}...`,
    );
    await new Promise((r) => setTimeout(r, 1000));
    count += 1;
    if (count == 600) {
      break;
    }
  }
  if (actual > HOW_MANY) {
    throw new Error(`Expected ${HOW_MANY} objects, but found ${actual}`);
  }
  if (actual !== HOW_MANY) {
    throw new Error(
      `Expected ${HOW_MANY} objects, found ${actual} after 10 minutes of waiting for async replication to complete`,
    );
  }
};

const cleanup = () =>
  weaviate
    .connectToLocal()
    .then((client) => client.collections.delete(COLLECTION_NAME));

const main = () =>
  weaviate
    .connectToLocal()
    .then(setup)
    .then(import_)
    .then(verify)
    .finally(cleanup);

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
