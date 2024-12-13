import docker


def get_env_variables(partial_image_name):
    client = docker.from_env()
    containers = client.containers.list()

    matching_containers = [
        container for container in containers if partial_image_name in container.image.tags[0]
    ]

    if not matching_containers:
        print(f"No running containers found matching image name: {partial_image_name}")
        return

    for container in matching_containers:
        print(f"Environment variables for container {container.short_id}:")
        exec_result = container.exec_run("env", stdout=True, stderr=True)
        print(exec_result.output.decode())
        print("--------------------------------------------")


get_env_variables("weaviate")
