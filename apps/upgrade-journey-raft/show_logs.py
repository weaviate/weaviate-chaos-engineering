from loguru import logger
import subprocess


def show_logs():
    result = subprocess.run(
        ["bash", "./scripts/show_logs_from_all_pods.sh"], stdout=subprocess.PIPE
    )
    logger.info(result.stdout.decode())


if __name__ == "__main__":
    show_logs()
