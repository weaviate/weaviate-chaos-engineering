#!/usr/bin/env python3
"""Record chaos-test results as JUnit XML (stdlib only, runs on bare runners).

Chaos tests are ad-hoc shell/python/go programs with no native JUnit output, so
CI jobs record one <testcase> per test into test-results.xml at the workspace
root — the same contract pytest gives the e2e repo — for consumption by the
weaviate-test-reporter action running later in the same job.

Subcommands:
  append      Record the result of one wrapped command (used by the
              run-chaos-test composite action). Reads the command's captured
              output to extract the failure culprit.
  report-job  Record one job-level result (used by report-job-junit in
              multi-step "journey" jobs). On failure it downloads this job's
              own log through the GitHub API — mid-run, which works because
              the failed step has already completed — and extracts the
              culprit from the failing step's section.

Culprit extraction knows three failure dialects (python traceback, go
panic/FAIL, docker build error) and cuts away the diagnostics dump that
common.sh's failure trap prints AFTER the real error, so the JUnit failure
body carries the cause rather than the tail of a container-log dump.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Valid XML 1.0 chars only (tab/newline/CR plus printable planes)
XML_INVALID_RE = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
LOG_TS_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+Z ?")

# explicit marker printed by common.sh's failure trap right before its dump
DIAG_MARKER = "===CHAOS-DIAGNOSTICS-BEGIN==="
# fallback for logs produced without the marker (scripts that don't source
# the current common.sh). "DIAGNOSTIC REPORT FOR:" is deliberately NOT a
# sentinel: wait_weaviate prints it BEFORE failing, as the primary diagnosis
DIAG_FALLBACK = "ABBREVIATED LOGS (first 30"

# bound how much of a (potentially huge) log is held in memory: culprits and
# the diagnostics dump both live at the tail
MAX_INPUT_BYTES = 16 * 1024 * 1024

PY_TRACEBACK = "Traceback (most recent call last):"
PY_CHAIN_RE = re.compile(
    r"During handling of the above exception|" r"The above exception was the direct cause"
)
GO_ANCHOR_RE = re.compile(r"panic: |fatal error: |--- FAIL|goroutine \d+ \[running\]")
DOCKER_ANCHOR_RE = re.compile(r"ERROR: failed to (build|solve)")

MESSAGE_RE = re.compile(
    r"Exception|[A-Za-z]\w*Error\b|\bERROR\b|\b[Ee]rror:|panic: |assert|FAIL|"
    r"[Ff]ailed|fatal|[Tt]imed? ?out|Timeout"
)
MESSAGE_NOISE_RE = re.compile(
    r"^\s*$|Retrying in |% complete|##\[|command terminated with exit code|"
    r'store logs|\[OK\]|=====|Node 20 is being deprecated|"Error":'
)

MAX_BODY_LINES = 400
MAX_MESSAGE_CHARS = 300


def sanitize(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = ANSI_RE.sub("", text)
    return XML_INVALID_RE.sub("", text)


def split_diagnostics(lines):
    """Return the lines before common.sh's failure-trap dump (if any)."""
    for i, line in enumerate(lines):
        if DIAG_MARKER in line:
            return lines[:i]
    for i, line in enumerate(lines):
        if DIAG_FALLBACK in line:
            # the dump is introduced by a bare "=====..." separator line
            cut = i
            if cut > 0 and set(lines[cut - 1].strip()) == {"="}:
                cut -= 1
            return lines[:cut]
    return lines


def _python_block_start(lines, last_tb):
    """Walk chained tracebacks upward from the last one."""
    start = last_tb
    while True:
        prev_tb = None
        for j in range(start - 1, -1, -1):
            if PY_TRACEBACK in lines[j]:
                prev_tb = j
                break
        if prev_tb is None:
            return start
        between = lines[prev_tb:start]
        if any(PY_CHAIN_RE.search(line) for line in between):
            start = prev_tb
        else:
            return start


def extract_culprit(text, tail_lines):
    """Return (message, body) for a failure, from sanitized raw output."""
    lines = split_diagnostics(sanitize(text).split("\n"))

    dialects = (
        ("python", lambda line: PY_TRACEBACK in line),
        ("go", GO_ANCHOR_RE.search),
        ("docker", DOCKER_ANCHOR_RE.search),
    )
    anchors = []
    for kind, matches in dialects:
        for i in range(len(lines) - 1, -1, -1):
            if matches(lines[i]):
                anchors.append((i, kind))
                break

    if anchors:
        idx, kind = max(anchors)
        if kind == "python":
            start = _python_block_start(lines, idx)
        elif kind == "docker":
            start = max(0, idx - 40)
        else:
            start = idx
        body_lines = lines[start:]
    else:
        body_lines = lines[-tail_lines:]
    body_lines = body_lines[-MAX_BODY_LINES:]

    message = None
    for candidates in (body_lines, lines):
        for line in reversed(candidates):
            stripped = line.strip()
            if not stripped or MESSAGE_NOISE_RE.search(stripped):
                continue
            if MESSAGE_RE.search(stripped):
                message = stripped[:MAX_MESSAGE_CHARS]
                break
        if message:
            break

    return message, "\n".join(body_lines).strip()


# ---------------------------------------------------------------- junit file


def load_or_create(path):
    if os.path.exists(path):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            # a corrupt file must not cost us this test's result
            os.replace(path, path + ".corrupt")
            return ET.Element("testsuites")
        if root.tag == "testsuite":  # tolerate a bare-suite file
            new_root = ET.Element("testsuites")
            new_root.append(root)
            root = new_root
        return root
    return ET.Element("testsuites")


def get_suite(root, name):
    for suite in root.findall("testsuite"):
        if suite.get("name") == name:
            return suite
    suite = ET.SubElement(root, "testsuite", name=name)
    return suite


def refresh_counts(root):
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for suite in root.findall("testsuite"):
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
        for case in suite.findall("testcase"):
            counts["tests"] += 1
            counts["time"] += float(case.get("time") or 0)
            if case.find("failure") is not None:
                counts["failures"] += 1
            if case.find("error") is not None:
                counts["errors"] += 1
            if case.find("skipped") is not None:
                counts["skipped"] += 1
        for key, value in counts.items():
            suite.set(key, f"{value:.3f}" if key == "time" else str(value))
            total[key] += value
    for key, value in total.items():
        root.set(key, f"{value:.3f}" if key == "time" else str(value))


def add_case(
    path, suite_name, case_name, classname, seconds, timestamp, result, message=None, body=None
):
    """result: one of 'pass', 'fail', 'timeout', 'error'."""
    root = load_or_create(path)
    suite = get_suite(root, suite_name)
    if timestamp and not suite.get("timestamp"):
        suite.set("timestamp", timestamp)

    case = ET.SubElement(
        suite,
        "testcase",
        name=case_name,
        classname=classname,
        time=f"{seconds:.3f}",
    )
    if result != "pass":
        tag = "error" if result == "error" else "failure"
        failure_type = {
            "fail": "TestFailure",
            "timeout": "Timeout",
            "error": "JobError",
        }[result]
        el = ET.SubElement(case, tag, type=failure_type)
        el.set("message", sanitize(message or "test failed"))
        el.text = sanitize(body or "")

    refresh_counts(root)
    ET.indent(root)
    tmp = path + ".tmp"
    ET.ElementTree(root).write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------- subcommands


DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_timeout_label(label):
    """GNU timeout duration ('55m', '90s', '2h') -> seconds, or None."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", (label or "").strip())
    return float(m.group(1)) * DURATION_UNITS[m.group(2)] if m else None


def _is_soft_timeout(args):
    """timeout(1) exits 124 (TERM honored) or 137 (KILL escalation, e.g. when
    common.sh's TERM trap outlives --kill-after). Guard on elapsed time so a
    test's own quick 124/137 exit is not misread as our soft timeout."""
    limit = parse_timeout_label(args.timeout_label)
    if not limit:
        return False
    return args.exit_code in (124, 137) and args.time >= limit * 0.95


def cmd_append(args):
    exit_code = args.exit_code
    if exit_code == 0:
        result, message, body = "pass", None, None
    else:
        output = ""
        if args.output_log and os.path.exists(args.output_log):
            size = os.path.getsize(args.output_log)
            with open(args.output_log, "rb") as f:
                if size > MAX_INPUT_BYTES:
                    f.seek(size - MAX_INPUT_BYTES)
                output = f.read().decode(errors="replace")
        message, body = extract_culprit(output, args.tail_lines)
        if _is_soft_timeout(args):
            result = "timeout"
            # keep the extracted evidence: a late exit 137 can also be a
            # genuine OOM kill, and even for a real hang the last output helps
            timeout_msg = f"test timed out after {args.timeout_label} (soft limit)"
            message = f"{timeout_msg}; last output: {message}" if message else timeout_msg
        else:
            result = "fail"
            message = message or f"exited with code {exit_code}"
        body = f"(exit code {exit_code})\n{body or ''}".strip()

    add_case(
        args.file,
        args.suite,
        args.name,
        args.classname or args.suite,
        args.time,
        args.timestamp,
        result,
        message,
        body,
    )
    print(f"chaos_junit: recorded {args.name} [{result}] in {args.file}")


def _api(url, token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_tail(resp):
    """Read a response in chunks, keeping only the last MAX_INPUT_BYTES —
    job logs can be huge and the failing step's section lives at the tail."""
    buf = bytearray()
    while True:
        chunk = resp.read(1024 * 1024)
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
        if len(buf) > 2 * MAX_INPUT_BYTES:
            del buf[:-MAX_INPUT_BYTES]


def _download_logs(url, token):
    """The /logs endpoint 302-redirects to blob storage, which rejects
    requests carrying the GitHub Authorization header — follow the redirect
    manually and fetch the signed URL without auth."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            return _read_tail(resp)
    except urllib.error.HTTPError as err:
        if err.code in (301, 302, 303, 307, 308):
            location = err.headers["Location"]
            with urllib.request.urlopen(location, timeout=60) as resp:
                return _read_tail(resp)
        raise


def find_current_job(token):
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    runner = os.environ.get("RUNNER_NAME", "")
    for page in range(1, 11):
        url = (
            f"{api}/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}"
            f"/jobs?per_page=100&page={page}"
        )
        jobs = json.loads(_api(url, token)).get("jobs", [])
        if not jobs:
            break
        for job in jobs:
            # only an in_progress job can be us; a completed job with the
            # same runner name is an earlier job on a recycled runner
            if job.get("runner_name") == runner and job.get("status") == "in_progress":
                return job
    return None


def failing_section(log_text, tail_lines):
    """Lines of the failing step: last ##[group]Run before the FIRST
    '##[error]Process completed' marker (later ##[error] lines are
    telemetry-action noise)."""
    lines = log_text.split("\n")
    end = None
    for i, line in enumerate(lines):
        if (
            "##[error]Process completed with exit code" in line
            or "##[error]The operation was canceled" in line
        ):
            end = i
            break
    if end is None:
        return lines[-tail_lines:]
    start = 0
    for i in range(end - 1, -1, -1):
        if "##[group]Run " in lines[i]:
            start = i
            break
    return lines[start:end][-2000:]


def cmd_report_job(args):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    job = None
    try:
        job = find_current_job(token) if token else None
    except Exception as exc:  # never fail the job over reporting
        print(f"chaos_junit: job lookup failed: {exc}", file=sys.stderr)

    seconds = 0.0
    timestamp = None
    job_url = None
    if job:
        job_url = job.get("html_url")
        started = job.get("started_at")
        if started:
            timestamp = started.rstrip("Z")
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            seconds = (datetime.now(timezone.utc) - start_dt).total_seconds()

    status = args.job_status.lower()
    if status == "success":
        result, message, body = "pass", None, None
    else:
        message, body = None, ""
        if job and token:
            for attempt in range(3):
                try:
                    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
                    repo = os.environ["GITHUB_REPOSITORY"]
                    raw = _download_logs(
                        f"{api}/repos/{repo}/actions/jobs/{job['id']}/logs", token
                    ).decode(errors="replace")
                    section = failing_section(raw, args.tail_lines)
                    section = [LOG_TS_PREFIX_RE.sub("", line) for line in section]
                    message, body = extract_culprit("\n".join(section), args.tail_lines)
                    break
                except Exception as exc:
                    print(
                        f"chaos_junit: log fetch attempt {attempt + 1} " f"failed: {exc}",
                        file=sys.stderr,
                    )
                    time.sleep(5)
        result = "error" if status == "cancelled" else "fail"
        message = message or f"job finished with status {status}"
        if job_url:
            body = f"{body}\n\nJob log: {job_url}".strip()

    add_case(
        args.file,
        args.suite,
        args.name,
        args.classname or args.suite,
        seconds,
        timestamp,
        result,
        message,
        body,
    )
    print(f"chaos_junit: recorded {args.name} [{result}] in {args.file}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", required=True)
    common.add_argument("--suite", required=True)
    common.add_argument("--name", required=True)
    common.add_argument("--classname", default=None, help="defaults to --suite")
    common.add_argument("--tail-lines", type=int, default=120)

    p = sub.add_parser("append", parents=[common])
    p.add_argument("--exit-code", type=int, required=True)
    p.add_argument("--time", type=float, default=0.0)
    p.add_argument("--timestamp", default=None)
    p.add_argument("--output-log", default=None)
    p.add_argument("--timeout-label", default="")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("report-job", parents=[common])
    p.add_argument("--job-status", required=True)
    p.set_defaults(func=cmd_report_job)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
