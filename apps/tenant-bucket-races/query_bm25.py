import weaviate
import weaviate.classes as wvc
import numpy as np

clients = [weaviate.connect_to_local(port=8080 + i, grpc_port=50051 + i) for i in range(3)]

cols = [client.collections.get("DataPoint") for client in clients]

tenants = list(cols[0].tenants.get().values())


for i in range(100000):
    col = cols[i % 3]
    tenant = tenants[i % len(tenants)]
    col = col.with_tenant(tenant.name)

    res = None
    for j in range(10):
        try:
            res = col.query.hybrid(
                query="the email of the you to from to the and your with",
                limit=500,
                vector=np.random.rand(1, 1536)[0].tolist(),
            )
        except Exception as e:
            print(e)

    try:
        col.tenants.update(
            [
                wvc.tenants.Tenant(
                    name=tenant.name,
                    activity_status=wvc.tenants.TenantActivityStatus.COLD,
                )
            ]
        )
    except Exception as e:
        print(e)

    try:
        if i % 10 == 0:
            print(i)
            print(res.objects[:10])
    except Exception as e:
        print(e)

# col = clients[0].collections.get("foo").query.hybrid()
