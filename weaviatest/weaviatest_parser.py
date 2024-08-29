import yaml
import subprocess
import sys
import time


# ANSI escape codes for colored output
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}==== {text} ===={Colors.ENDC}\n")


def print_test_result(test_name, result, time_taken):
    color = Colors.OKGREEN if result == "PASSED" else Colors.FAIL
    print(
        f"{color}{Colors.BOLD}Test '{test_name}': {result} ({time_taken:.2f} seconds){Colors.ENDC}\n"
    )


def print_step_execution(step_name):
    print(
        f"{Colors.OKBLUE}{Colors.BOLD}>>> Executing step: {Colors.ENDC}{Colors.OKCYAN}{step_name}{Colors.ENDC}"
    )


def print_command_output(output):
    print(f"{Colors.OKCYAN}{output.strip()}{Colors.ENDC}")


def run_command(command):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    print_command_output(result.stdout)
    return result.returncode


def run_command_with_retry(command, retries=5, delay=1):
    total_wait = 0
    print(f"{Colors.WARNING}Wait for command, retries = {retries}, delay = {delay}{Colors.ENDC}")
    for _ in range(retries):
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        print_command_output(result.stdout)
        if result.returncode == 0:
            break
        time.sleep(delay)
        total_wait += delay
    if result.returncode != 0:
        print(
            f"{Colors.FAIL}Command still failed after retrying {delay * retries} seconds{Colors.ENDC}"
        )
    else:
        print(f"{Colors.OKGREEN}Command succeeded after {total_wait} seconds{Colors.ENDC}")
    return result.returncode


def run_step(step):
    if "wait" in step:
        return run_command_with_retry(
            step["wait"], retries=step.get("retries", 5), delay=step.get("delay", 1)
        )
    elif "weaviatest" in step:
        docker_cmd = "docker run --network=host weaviatest "
        return run_command(docker_cmd + step["weaviatest"])
    else:
        return run_command(step["command"])


def run_test_case(test_file, test_name=None):
    results = {}  # Dictionary to store test results

    with open(test_file, "r") as stream:
        try:
            data = yaml.safe_load(stream)
            print_header(f"Running test collection: {data['name']}")
            for test in data["tests"]:
                if test_name and test["name"] != test_name:
                    continue

                pre_passed = True
                passed = True
                print_header(f"Running test: {test['name']}")
                start_time = time.time()  # Start time of the test case

                # Pre-test steps
                if "pre" in test:
                    print(f"{Colors.OKBLUE}{Colors.BOLD}Executing pre-test steps{Colors.ENDC}")
                    for step in test["pre"]:
                        print_step_execution(step["name"])
                        if run_step(step) != 0:
                            print(
                                f"{Colors.FAIL}Pre-test step failed. Skipping remaining steps.{Colors.ENDC}"
                            )
                            pre_passed = False
                            break

                if pre_passed:
                    # Main steps
                    print(f"{Colors.OKBLUE}{Colors.BOLD}Executing main test steps{Colors.ENDC}")
                    for step in test["steps"]:
                        print_step_execution(step["name"])
                        if run_step(step) != 0:
                            print(
                                f"{Colors.FAIL}Step failed. Executing post-test steps{Colors.ENDC}"
                            )
                            passed = False
                            break

                # Post-test steps
                print(f"{Colors.OKBLUE}{Colors.BOLD}Executing post-test steps{Colors.ENDC}")
                if "post" in test:
                    for step in test["post"]:
                        print_step_execution(step["name"])
                        run_step(step)

                if "common" in data:
                    print(
                        f"{Colors.OKBLUE}{Colors.BOLD}Executing common post-test steps{Colors.ENDC}"
                    )
                    for step in data["common"]:
                        print_step_execution(step["name"])
                        run_step(step)

                end_time = time.time()  # End time of the test case
                execution_time = end_time - start_time  # Calculate execution time

                result = "PASSED" if passed and pre_passed else "FAILED"
                results[test["name"]] = {"state": result, "time": execution_time}
                print_test_result(test["name"], result, execution_time)

        except yaml.YAMLError as exc:
            print(exc)

    # Print summary of test results
    print_header("Test Results")
    for test_name, result in results.items():
        print_test_result(test_name, result["state"], result["time"])

    if any(result["state"] == "FAILED" for result in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    test_file = sys.argv[1]
    test_name = sys.argv[2] if len(sys.argv) > 2 else None
    run_test_case(test_file, test_name)
