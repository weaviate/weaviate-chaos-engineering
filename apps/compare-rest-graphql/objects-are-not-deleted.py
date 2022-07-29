"""
https://semi-technology.atlassian.net/browse/WEAVIATE-151
"""

import weaviate
from loguru import logger
import sys, traceback, time, os

class DiscrepancyError(Exception):
  """ Raised on REST and GraphQL mismatch """
  pass

def create_weaviate_schema():
    schema = {
        "classes": [
            {
                "class": "ObjectsAreNotDeleted",
                "vectorizer": "none",
                "vectorIndexType": "hnsw",
                "invertedIndexConfig": {
                  "bm25": {
                    "b": 0.75,
                    "k1": 1.2
                  },
                  "cleanupIntervalSeconds": 60,
                  "stopwords": {
                    "preset": "en"
                  }
                },
                "properties": [
                    {
                        "dataType": [
                            "string"
                        ],
                        "name": "description",
                        "tokenization": "word",
                        "indexInverted": True
                    },
                ]
            },
        ]
    }
    # add schema
    if not client.schema.contains(schema):
        client.schema.create(schema)

def get_body(index: str, req_type: str):
    return { "description": f"this is an update number: {index} with {req_type}" }

def create_object_if_it_doesnt_exist(class_name: str, object_id: str):
  try:
    exists = client.data_object.exists(object_id)
    graphQLExists = existsGraphQLGet(object_id)
    if not exists:
      if graphQLExists:
        logger.info(f"Mismatch exists: head: {exists} graphql: {graphQLExists}")
        raise Exception(f"Mismatch for: {object_id} exists: head: {exists} graphql: {graphQLExists}")
      client.data_object.create(get_body(0, "create"), class_name, object_id)
  except Exception as e:
      logger.error(f"Error adding {class_name} object - id: {object_id}")
      logger.error(e)
      logger.error(''.join(traceback.format_tb(e.__traceback__)))
      raise e

def existsGraphQLGet(object_id: str) -> bool:
  equalId = { "path": ["id"], "operator": "Equal", "valueString": object_id }
  objectGraphQLGet = (
    client.query
    .get("ObjectsAreNotDeleted", ['_additional{id}'])
    .with_where(equalId)
    .do()
  )
  return len(objectGraphQLGet['data']['Get']['ObjectsAreNotDeleted']) > 0

def search_object(object_id: str, should_exist: bool):
    headExists = client.data_object.exists(object_id)
    objectGetById = client.data_object.get_by_id(object_id)
    equalId = { "path": ["id"], "operator": "Equal", "valueString": object_id }
    objectGraphQLGet = (
      client.query
      .get("ObjectsAreNotDeleted", ['_additional{id}'])
      .with_where(equalId)
      .do()
    )

    objectRestExists = objectGetById is not None and len(objectGetById) > 0
    objectGraphQLCount = len(objectGraphQLGet['data']['Get']['ObjectsAreNotDeleted'])
    objectGraphQLExists = objectGraphQLCount > 0
    if not (should_exist == headExists and should_exist == objectRestExists and should_exist == objectGraphQLExists):
        return f"search {object_id}: should_exist: {should_exist} head: {headExists} rest: {objectRestExists} graphql: {objectGraphQLExists}"
    if should_exist and objectGraphQLCount > 1:
      return f"COUNT: {objectGraphQLCount} {object_id}: should_exist: {should_exist} head: {headExists} rest: {objectRestExists} graphql: {objectGraphQLExists}"
    return ""

def search_objects(uuids, should_exist: bool):
  discrepancies = []
  for id in uuids:
    res = search_object(id, should_exist)
    if len(res) > 0:
      discrepancies.append(res)

  if len(discrepancies) > 0:
    logger.error("Discrepancies:")
    for msg in discrepancies:
      logger.error(msg)
    raise DiscrepancyError(f"Discrepancy between REST and GraphQL detected")

def delete_all_in_batch(uuids):
  try:
    operands = []
    for id in uuids:
      operands.append({
        'operator': 'Equal',
        'path': ['id'],
        'valueString': id
      })
    where = {
      'operator': 'Or',
      'operands': operands
    }
    client.batch.delete_objects("ObjectsAreNotDeleted", where, 'minimal', False)
  except Exception as e:
    logger.error(f"Error batch deleting {class_name} objects")
    logger.error(e)
    logger.error(''.join(traceback.format_tb(e.__traceback__)))
    raise Exception('Error occured during batch delete')

def getUUIDs(param: str):
  uuids1 = [
        "17e9828d-ff0a-5e47-8101-b52312345670",
        "17e9828d-ff0a-5e47-8101-b52312345671",
        "17e9828d-ff0a-5e47-8101-b52312345672",
        "17e9828d-ff0a-5e47-8101-b52312345673",
        "17e9828d-ff0a-5e47-8101-b52312345674",
        "17e9828d-ff0a-5e47-8101-b52312345675",
        "17e9828d-ff0a-5e47-8101-b52312345678"
      ]
  uuids2 = [
        "07e9828d-ff0a-5e47-8101-b52312345670",
        "07e9828d-ff0a-5e47-8101-b52312345671",
        "07e9828d-ff0a-5e47-8101-b52312345672",
        "07e9828d-ff0a-5e47-8101-b52312345673",
        "07e9828d-ff0a-5e47-8101-b52312345674",
        "07e9828d-ff0a-5e47-8101-b52312345675",
        "07e9828d-ff0a-5e47-8101-b52312345678"
      ]
  uuids3 = ["00000000-ff0a-5e47-8101-b52312345670"]
  if param == "0":
    return uuids1
  if param == "1":
    return uuids2
  if param == "3":
    return uuids3
  uuids1.extend(uuids2)
  return uuids1

if __name__ == "__main__":
    max_attempts = int(os.getenv("MAX_ATTEMPTS", 100)) 
    for attempt in range(max_attempts):
      try:
        client = weaviate.Client("http://localhost:8080")
        param = sys.argv[1]
        logger.info(f"Attempt {attempt} Running with param: {param}")

        class_name = "ObjectsAreNotDeleted"
        uuids = getUUIDs(param)
        create_weaviate_schema()
        loops = 100
        for i in range(loops):
          logger.info(f"RUN number: {i}")
          for id in uuids:
            create_object_if_it_doesnt_exist(class_name, id)
          search_objects(uuids, True)
          delete_all_in_batch(uuids)
          search_objects(uuids, False)
      except DiscrepancyError:
        logger.error(sys.exc_info()[1])
        logger.error(''.join(traceback.format_tb(sys.exc_info()[2])))
        exit(1)
      except Exception as e:
        logger.info(f"Attempt {attempt}: Caught exception {e} retrying")
        time.sleep(2)

