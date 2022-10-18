import itertools
import subprocess


def get_version_tags():
    res = subprocess.run(['bash', './get_tags.sh'], capture_output=True)
    if res.returncode != 0:
        raise Exception(f"failed to get weaviate version tags: {res.stderr.decode('utf-8')}")

    # script returns newline-delineated tags. after the
    # split, the last newline produces an empty string
    return res.stdout.decode('utf-8').split('\n')[:-1]

def is_version_since_backup_introduced(tag):
    # backups were introduced starting from v1.15.0
    parts = tag.split('.')
    if int(parts[0]) > 1 or int(parts[1]) >= 15:
        return True
    return False

def generate_version_pairs():
    try:
        tags = get_version_tags()
    except Exception as e:
        return e

    # TODO: the version combinations grow exponentially with each
    #       new version. we should put a cap on this, and only test
    #       the last N versions for compatibility
    versions = [t.lstrip('v') for t in tags if is_version_since_backup_introduced(t.lstrip('v'))]

    # compute all possible version combinations
    combos = list(itertools.combinations(versions, 2))

    # make permutation for each pair so we check all version pairs
    permut = [list(itertools.permutations(pair)) for pair in combos]

    # flatten the list of all version pairs
    flat = list(itertools.chain.from_iterable(permut))

    # more easily consumable by a shell script
    return ' '.join([f"{left}+{right}" for (left, right) in flat])


if __name__ == "__main__":
    print(generate_version_pairs())
