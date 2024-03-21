import itertools
import os
import subprocess


class VersionPairsException(Exception):
    """
    Used to indicate to the calling shell script that things didn't go as planned.

    The script expects to find `failed` somewhere in the error message, so the
    provided error message is prefixed with this.

    Only the first argument to the constructor is used in the error message. If no
    message is provided, the caller is notified with a default one. But ideally, a
    message should be provided.
    """

    def __init__(self, *args):
        if len(args) == 0:
            args = ("with no message provided",)
        msg = f"failed: {args[0]}"
        super().__init__(msg)


def get_version_tags():
    res = subprocess.run(["bash", "./get_tags.sh"], capture_output=True)
    if res.returncode != 0:
        raise VersionPairsException(f"get weaviate version tags: {res.stderr.decode('utf-8')}")

    # script returns newline-delineated tags. after the
    # split, the last newline produces an empty string
    return res.stdout.decode("utf-8").split("\n")[:-1]


def is_version_since_backup_introduced(tag):
    # restoring backups older than 1.17.0 isn't possible after 1.23.0
    parts = tag.split(".")
    if int(parts[0]) > 1 or int(parts[1]) >= 17:
        return True
    return False


def generate_version_pairs():
    try:
        tags = get_version_tags()
        target = target_version()
    except Exception as e:
        return e

    versions = [t.lstrip("v") for t in tags if is_version_since_backup_introduced(t.lstrip("v"))]

    # compare the latest version with all existing backup-supporting versions
    pairs = [(prev, target) for prev in versions]

    # more easily consumable by a shell script
    return " ".join([f"{left}+{right}" for (left, right) in pairs])


def target_version():
    vers = os.environ.get("WEAVIATE_VERSION")
    if vers == "":
        raise VersionPairsException('"WEAVIATE_VERSION" not set')
    return vers


if __name__ == "__main__":
    print(generate_version_pairs())
