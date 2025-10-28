#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10,<4"
# ///
from __future__ import annotations
from argparse import ArgumentParser
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Literal, Self, Sequence
from zipfile import ZipFile
import subprocess as sp
import shlex
import logging
import tempfile
import os
from urllib.request import urlretrieve

logger = logging.getLogger(__name__)
ZIP_URL_TEMPLATE = "https://github.com/ome/ngff/archive/v{version}.zip"


@dataclass
class TestCase:
    description: str | None
    schema_id: str
    tests: list[Test]

    @classmethod
    def from_jso(cls, jso: dict[str, Any]) -> Self:
        return cls(
            jso.get("description"),
            jso["schema"]["id"],
            [Test.from_jso(t) for t in jso["tests"]],
        )


@dataclass
class Test:
    formerly: str | None
    description: str | None
    data: dict
    valid: bool

    @classmethod
    def from_jso(cls, jso: dict[str, Any]) -> Self:
        return cls(
            jso.get("formerly"),
            jso.get("description"),
            jso["data"],
            bool(jso["valid"]),
        )


@dataclass
class TestData:
    case_description: str | None
    schema_id: str
    version: str
    file_name: str
    index: int
    formerly: str | None
    description: str | None
    zarr_attributes_str: str
    valid: bool

    def slug(self) -> str | None:
        if self.formerly:
            s = self.formerly.split("/")[-1].lower()
            suff = ".json"
            if s.endswith(suff):
                s = s[: -len(".json")]
            return s
        if self.description and self.description != "TBD":
            return "_".join(self.description.lower().split())

        return None

    def file_stem(self) -> str:
        s = self.file_name.lower()
        for suff in (".json", "_suite"):
            if s.endswith(suff):
                s = s[: -len(suff)]
        return s

    def schema_name(self) -> str:
        s = self.schema_id.lower()
        for suff in (".json", ".schema"):
            if s.endswith(suff):
                s = s[: -len(suff)]
        for pref in ("schemas/",):
            if s.startswith(pref):
                s = s[len(pref) :]
        return s

    def test_id(self) -> str:
        return f"v{self.version.replace('.', '_')}:{self.file_stem()}:{self.index}:{self.slug()}"


@dataclass
class CommandOutput:
    valid: bool
    message: str | None

    @classmethod
    def from_jso(cls, jso: dict[str, Any]) -> Self:
        return cls(jso["valid"], jso.get("message"))


@dataclass
class TestResult:
    test_data: TestData
    status: Literal["pass", "fail", "error"]
    message: str | None
    stderr: str
    return_code: int


@dataclass
class VersionTestData:
    version: str
    test_cases: dict[str, TestCase]
    """Map from filename to test case information"""

    # examples: dict[str, dict]
    # """Map from filename to zarr metadata dict"""

    # def iter_examples(self) -> Iterable[ExampleData]:
    #     for k, v in self.examples.items():
    #         yield ExampleData(self.version, k, json.dumps(v))

    def iter_tests(self) -> Iterable[TestData]:
        for file_name, test_case in self.test_cases.items():
            for idx, test in enumerate(test_case.tests):
                yield TestData(
                    test_case.description,
                    test_case.schema_id,
                    self.version,
                    file_name,
                    idx,
                    test.formerly,
                    test.description,
                    json.dumps(test.data),
                    test.valid,
                )


def cache_dir() -> Path:
    """
    A pale imitation of https://github.com/tox-dev/platformdirs for major desktop OSs.
    """
    platform = sys.platform
    if platform == "linux":
        return (
            Path(os.getenv("XDG_CACHE_HOME", "~/.cache")).expanduser()
            / "ome_zarr_conformance"
        )
    if platform == "darwin":
        return Path("~/Library/Caches").expanduser() / "ome_zarr_conformance"
    if platform == "win32":
        appdata = os.getenv("APPDATA")
        if appdata is not None:
            return Path(appdata).joinpath("ome/ome_zarr_conformance")

    return Path(tempfile.gettempdir()).joinpath("ome/ome_zarr_conformance")


def retrieve(version: str) -> Path:
    fname = version.replace(".", "_") + ".zip"
    fdir = cache_dir()
    fpath = fdir / fname
    if not fpath.is_file():
        fdir.mkdir(exist_ok=True, parents=True)
        url = ZIP_URL_TEMPLATE.format(version=version)
        urlretrieve(url, fpath)
    return fpath


def get_data(version: str) -> VersionTestData:
    p = retrieve(version)
    test_cases = dict()
    logger.debug("Reading zip file %s", p)
    n_tests = 0
    # examples = dict()
    with ZipFile(p, mode="r") as z:
        for info in z.infolist():
            name = info.filename.split("/", 1)[1]
            suffix = ".json"
            if not name.endswith(suffix):
                logger.debug("Skipping non-JSON file %s", name)
                continue

            test_prefix = "tests/"
            if name.startswith(test_prefix):
                with z.open(info) as f:
                    test_case = TestCase.from_jso(json.load(f))
                test_cases[name[len(test_prefix) :]] = test_case
                n_tests += len(test_case.tests)
            else:
                logger.debug("Skipping non-test file %s", name)
            # elif name.startswith("examples/") and not name.rsplit("/", maxsplit=1)[
            #     -1
            # ].startswith("."):
            #     with z.open(info) as f:
            #         examples[name] = json.load(f)
    logger.info("Got %s tests in %s test cases", n_tests, len(test_cases))
    return VersionTestData(version, test_cases)  # , examples)


def run_test(cmd: list[str], test_data: TestData) -> TestResult:
    res = sp.run([*cmd, test_data.zarr_attributes_str], capture_output=True, text=True)
    if res.returncode:
        return TestResult(test_data, "error", None, res.stderr, res.returncode)
    out = CommandOutput.from_jso(json.loads(res.stdout))
    if out.valid == test_data.valid:
        return TestResult(test_data, "pass", out.message, res.stderr, res.returncode)
    else:
        return TestResult(test_data, "fail", out.message, res.stderr, res.returncode)


def run_tests(
    cmd: list[str],
    data: Sequence[VersionTestData],
    includes: list[re.Pattern],
    excludes: list[re.Pattern],
    threads=None,
) -> Iterable[TestResult]:
    with ThreadPoolExecutor(threads) as pool:
        futs: list[Future] = []
        for d in data:
            for t in d.iter_tests():
                test_id = t.test_id()

                if includes:
                    include = False
                    for p in includes:
                        if p.search(test_id):
                            include = True
                            break
                else:
                    include = True

                if include:
                    for p in excludes:
                        if p.search(test_id):
                            include = False
                            break

                if include:
                    futs.append(pool.submit(run_test, cmd, t))

        for f in futs:
            yield f.result()


def main(raw_args: None | list[str] = None):
    parser = ArgumentParser()
    parser.add_argument(
        "--ome-zarr-version",
        "-o",
        action="append",
        help="which OME-Zarr versions to test with the given command; can be given multiple times (default all available)",
    )
    parser.add_argument(
        "--no-exit-code",
        "-X",
        action="store_true",
        help="return a 'success' exit code even if tests failed",
    )
    parser.add_argument(
        "--include-pattern",
        "-p",
        type=re.compile,
        action="append",
        help="regular expression pattern for tests to include",
    )
    parser.add_argument(
        "--exclude-pattern",
        "-P",
        type=re.compile,
        action="append",
        help="regular expression pattern for tests to exclude",
    )
    parser.add_argument(
        "command",
        type=shlex.split,
        help="command which will try to parse and validate the given JSON",
    )
    args = parser.parse_args(raw_args)
    logging.basicConfig(level=logging.INFO)
    logging.debug("Got args: %s", args)

    if not args.ome_zarr_version:
        versions = ["0.4", "0.5"]
    else:
        versions = args.ome_zarr_version

    vtd = [get_data(v) for v in versions]

    passes = 0
    failures = 0
    errors = 0
    for res in run_tests(
        args.command, vtd, args.include_pattern or [], args.exclude_pattern or []
    ):
        test_id = res.test_data.test_id()
        row = [
            res.test_data.test_id(),
            res.status,
        ]
        if res.status == "pass":
            passes += 1
        elif res.status == "fail":
            failures += 1
            logger.warning(
                "Test %s failed (expected %svalid): %s%s",
                test_id,
                "" if res.test_data.valid else "in",
                res.message or "",
                "\n" + res.stderr if res.stderr else "",
            )
        elif res.status == "error":
            errors += 1
            logger.error(
                "Test %s errored (code %s):%s",
                test_id,
                res.return_code,
                "\n" + res.stderr if res.stderr else "",
            )

        print("\t".join(row))

    logger.info("Got %s passes, %s failures, %s errors", passes, failures, errors)

    if args.no_exit_code:
        sys.exit(0)

    code = 0
    if failures:
        code += 1
    if errors:
        code += 2
    sys.exit(code)


if __name__ == "__main__":
    main()
