> This repository is archived as the functionality has been rolled into
> <https://github.com/ome/ngff-spec>

# OME-Zarr conformance tests

A python-based tool to run arbitrary OME-Zarr metadata implementations against a full suite of conformance tests.

Supports OME-Zarr version 0.4 and above.

## Usage

[`uv` is recommended](https://docs.astral.sh/uv/getting-started/installation/),
although pipx or pip may work for your use case.

Install the tool for your user with `uv tool install https://github.com/clbarnes/ome_zarr_conformance.git`.

Wrap your OME-Zarr metadata implementation in a dingus CLI program which takes as an argument
a JSON string representing the Zarr attributes of an OME-Zarr dataset (Zarr group).

> Zarr attributes are found in the `"attributes"` key of a Zarr v3 metadata document,
> or in the `.zattrs` file for a Zarr v2 group prefix.
>
> In OME-Zarr v0.4, OME-Zarr metadata keys are directly in the top-level attributes,
> so your payload may look like `{"plate": {"version": "0.4", ... }}`.
> In OME-Zarr v0.5 and above, OME-Zarr metadata keys are beneath an `"ome"` key,
> so your payload may look like `{"ome": {"version": "0.5", "plate": { ... }}}`.

The program should attempt to parse that string and print to STDOUT another JSON string, with fields

- `"valid"`: boolean, `true` if the payload was compliant with the OME-Zarr specification
- optionally `"message"`: `null` or string with arbitrary further details

The program should not error for invalid OME-Zarr metadata, but may error if malformed JSON is given.

A fictional OME-Zarr metadata implementation based on python/ pydantic could provide a dingus CLI like this:

```python
#!/usr/bin/env python3
import sys
import json

from my_ome_zarr_pydantic.v0_5 import OMEZarrGroupAttrs, InvalidOMEZarrException

try:
    OMEZarrGroupAttrs.model_validate_json(sys.argv[1])
    out = {"valid": True}
except InvalidOMEZarrException as e:
    out = {"valid": False, "message": str(e)}

print(json.dumps(out))
```

For such a program at `path/to/my_implementation_wrapper`, call

```sh
ome-zarr-conformance path/to/my_implementation_wrapper
```

to download all test cases and run them.
Downloads are cached for subsequent runs.

See `ome-zarr-conformance --help` for further information (e.g. restricting to specific OME-Zarr versions).

If your CLI needs additional arguments, wrap the full call in quotes appropriate for your shell, e.g.

```sh
ome-zarr-conformance --ome-zarr-version 0.5 "path/to/my_implementation_wrapper -version v0.5 -quiet -fromJson"
```

The quoted CLI call will be split according to POSIX rules,
and the JSON payload will be appended as a final argument (effectively in quotes).

Results are printed to STDOUT as a test ID and `pass`/ `fail`/ `error`, separated by a tab character.
The test ID contains the OME-Zarr version (e.g. `v0_5`), name of the test case, index of the test in the case,
and some identifying test slug where possible.
Failures and errors are logged to STDERR, with any message if given and any captured standard error output.

N.B. while this is packaged to be a pip-installable tool, you can also just vendorise `src/ome_zarr_conformance/ome_zarr_conformance.py` -
it works as a standalone script.

## Motivations

JSON Schema is fine for specifying the rough shape of data at the level of individual fields,
but is not sufficient for schema-level rules like "field `a` must have the same length as field `b`".

There are going to be many implementations of the OME-Zarr specification,
across multiple languages,
so a lot of duplicated effort for writing integration tests (or worse, not spending that effort!).
These can act as integration tests for any implementation by writing a single trivial CLI,
and zero additional work for implementation maintainers as the test suite grows.

## Limitations

This tool only handles one Zarr attributes object at a time, and only that one Zarr attributes object.
There are a number of rules in the spec which cannot be validated in this way, for example those relating to

- zarr hierarchies
  (e.g. that a string is a valid path to another zarr node,
  or that the OME well group exists below an OME plate group)
- zarr arrays (e.g. that an array has a particular `shape` or `data_type`)

This is by design, so that OME-Zarr metadata implementations can stay independent of Zarr implementations,
and to greatly simplify the fixtures.

### Dingus CLIs for permissive readers

Some implementations are very permissive readers - they may deserialise to an unstructured format
(e.g. python dicts of dicts) and then only look up attributes when they need them.
This could cause false negatives for text fixtures containing invalid OME-Zarr metadata.

Such libraries should still be able to validate metadata to prevent invalid writes
(or make it impossible to represent invalid metadata);
the dingus may be a little more complicated to use the write-validation rather than just the parse-validation.
