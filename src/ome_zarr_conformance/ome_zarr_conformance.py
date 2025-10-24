#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10,<4"
# dependencies = [
#     "pooch>=1.8.2",
#     "pydantic>=2.12.3",
# ]
# ///
from __future__ import annotations
from argparse import ArgumentParser
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Literal, Sequence
from zipfile import ZipFile
import pooch
import subprocess as sp
import shlex
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)
ZIP_URL_TEMPLATE = "https://github.com/ome/ngff/archive/v{version}.zip"


class TestCase(BaseModel):
    description: str | None = None
    schema_: Schema = Field(alias="schema")
    tests: list[Test]


class Schema(BaseModel):
    id: str


class Test(BaseModel):
    formerly: str | None = None
    description: str | None = None
    data: dict
    valid: bool


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

    def slug(self) -> str:
        return f"{self.case_description or ''}|{self.description or ''}|{self.formerly or ''}"


class CommandOutput(BaseModel):
    valid: bool
    message: str | None = None


@dataclass
class TestResult:
    test_data: TestData
    status: Literal["pass", "fail", "error"]
    message: str | None
    stderr: str
    return_code: int


# @dataclass
# class ExampleData:
#     version: str
#     file_name: str
#     zarr_metadata_str: str


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
                    test_case.schema_.id,
                    self.version,
                    file_name,
                    idx,
                    test.formerly,
                    test.description,
                    json.dumps(test.data),
                    test.valid,
                )


def retrieve(version: str):
    url = ZIP_URL_TEMPLATE.format(version=version)
    path = pooch.retrieve(
        url, None, progressbar=True, path=pooch.os_cache("ome_zarr_conformance")
    )
    return Path(path)


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
                    test_case = TestCase.model_validate_json(f.read())
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
    out = CommandOutput.model_validate_json(res.stdout)
    if out.valid == test_data.valid:
        return TestResult(test_data, "pass", out.message, res.stderr, res.returncode)
    else:
        return TestResult(test_data, "fail", out.message, res.stderr, res.returncode)


def run_tests(
    cmd: list[str], data: Sequence[VersionTestData], threads=None
) -> Iterable[TestResult]:
    with ThreadPoolExecutor(threads) as p:
        futs: list[Future] = []
        for d in data:
            for t in d.iter_tests():
                futs.append(p.submit(run_test, cmd, t))
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
        "command",
        type=shlex.split,
        help="command which will try to parse and validate the given JSON",
    )
    parser.add_argument("--no-header", "-H", action="store_true")
    args = parser.parse_args(raw_args)
    logging.basicConfig(level=logging.INFO)
    logging.debug("Got args: %s", args)

    if not args.ome_zarr_version:
        versions = ["0.4", "0.5"]
    else:
        versions = args.ome_zarr_version

    vtd = [get_data(v) for v in versions]
    headers = [
        "version",
        "file_name",
        "schema_id",
        "test_index",
        "expect_valid",
        "status",
        "description",
        "return_code",
        "message",
    ]
    if not args.no_header:
        print("\t".join(headers))

    passes = 0
    failures = 0
    errors = 0
    for res in run_tests(args.command, vtd):
        row = [
            res.test_data.version,
            res.test_data.file_name,
            res.test_data.schema_id,
            str(res.test_data.index),
            str(res.test_data.valid).lower(),
            res.status,
            res.test_data.slug(),
            str(res.return_code),
            res.message or "",
        ]
        if res.status == "pass":
            passes += 1
        elif res.status == "fail":
            failures += 1
        elif res.status == "error":
            errors += 1

        print("\t".join(row))

    logger.info("Got %s passes, %s failures, %s errors", passes, failures, errors)

    code = 0
    if failures:
        code += 1
    if errors:
        code += 2
    sys.exit(code)


if __name__ == "__main__":
    main()
