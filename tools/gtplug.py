import os

OUT = os.environ["GT_OUT"]
_failed = set()


def pytest_runtest_logreport(report):
    if report.failed:
        _failed.add(report.nodeid.split(" ")[0])


def pytest_collectreport(report):
    if report.failed:
        _failed.add("COLLECT:" + report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    with open(OUT, "w") as fh:
        fh.write("\n".join(sorted(_failed)) + "\n")
