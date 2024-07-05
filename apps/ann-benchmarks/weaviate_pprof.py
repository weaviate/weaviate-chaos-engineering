import subprocess
import re


def obtain_heap_profile(origin):
    ping = subprocess.run(
        ["go", "tool", "pprof", "-top", "-unit=MB", f"{origin}/debug/pprof/heap"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    for line in ping.stdout.split("\n"):
        if "MB total" in line:
            result = re.search(r"of (\b[0-9]+\.[0-9]+)MB total", line)
            groups = result.groups()
            if len(groups) == 1:
                return float(groups[0])

    raise Exception("could not extract heap value")
