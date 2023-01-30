import weaviate
import time

client = weaviate.Client("http://localhost:8080")

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
]

for scenario in scenarios:
    print(scenario["name"], end='')
    before = time.time()
    query_result = (
      client.query
      .get("Set", "_additional{ id }")
      .with_limit(10)
      .with_where(scenario["where_filter"])
      .do()
    )
    after = time.time()
    print(f" ...query took {(after-before)}s")
    print(f"")



