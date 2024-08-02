import weaviate
import weaviate.classes as wvc
import time
import random

client = weaviate.connect_to_local()
col = client.collections.get("DataPoint")

tenants = list(col.tenants.get().values())

for i in range(100_000):
    try:
        status = wvc.tenants.TenantActivityStatus.HOT
        if i % 2 == 0:
            status = wvc.tenants.TenantActivityStatus.COLD

        tenant = tenants[i % len(tenants)]
        col.tenants.update([wvc.tenants.Tenant(name=tenant.name, activity_status=status)])
        time.sleep(random.random() * 0.01)
    except Exception as e:
        print(e)
