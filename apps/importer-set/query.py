import weaviate
import time
import random
import uuid

client = weaviate.Client("http://localhost:8080")

min_object=0
max_object=4_000_000

def random_existing_uuid():
    return str(uuid.UUID(int=random.randint(min_object, max_object)))

scenarios = [
    {
        "name": "bool_prop_1 == true OR bool_prop_2 == true",
        "where_filter" :{
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueBoolean": True,
                "path": ["prop_2"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "bool_prop_1 == true AND bool_prop_2 == true",
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueBoolean": True,
                "path": ["prop_2"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "bool_prop_1 == true OR int_prop == 0",
        "where_filter" :{
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "bool_prop_1 == true AND int_prop == 0",
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "bool_prop_1 == true AND int_prop >= 0",
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"GreaterThanEqual",
              }]
        }
    },
    {
        "name": "bool_prop_1 == true OR int_prop >= 0",
        "where_filter" :{
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"GreaterThanEqual",
              }]
        }
    },
    {
        "name": "unfiltered vector search",
        "near_object": {
            "id": random_existing_uuid()
        },
        "new_paragraph": True,
    },
    {
        "name": "vector search + (bool_prop_1 == true OR bool_prop_2 == true)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter": {
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueBoolean": True,
                "path": ["prop_2"],
                "operator":"Equal",
              }]
        },
        "new_paragraph": True,
    },
    {
        "name": "vector search + (bool_prop_1 == true AND bool_prop_2 == true)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueBoolean": True,
                "path": ["prop_2"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "vector search + (bool_prop_1 == true OR int_prop == 0)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter" :{
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "vector search + (bool_prop_1 == true AND int_prop == 0)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"Equal",
              }]
        }
    },
    {
        "name": "vector search + (bool_prop_1 == true AND int_prop >= 0)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter" :{
          "operator": "And",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"GreaterThanEqual",
              }]
        }
    },
    {
        "name": "vector search + (bool_prop_1 == true OR int_prop >= 0)",
        "near_object": {
            "id": random_existing_uuid()
        },
        "where_filter" :{
          "operator": "Or",
          "operands": [{
                "valueBoolean": True,
                "path": ["prop_1"],
                "operator":"Equal",
              }, {
                "valueInt": 0,
                "path": ["modulo_11"],
                "operator":"GreaterThanEqual",
              }]
        }
    },
]

for scenario in scenarios:
    if "new_paragraph" in scenario:
        print(f"")

    print(scenario["name"], end='')
    before = time.time()
    q = ( 
      client.query 
        .get("Set", "_additional{ id }")
        .with_limit(10)
    )

    if "where_filter" in scenario:
      q = q.with_where(scenario["where_filter"])

    if "near_object" in scenario:
      q = q.with_near_object(scenario["near_object"])

    query_result = q.do()
    after = time.time()
    print(f" ...query took {(after-before)}s")



