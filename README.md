# OME-Zarr conformance tests

A python-based tool to run arbitrary OME-Zarr metadata implementations against a full suite of conformance tests.

Supports OME-Zarr version 0.4 and above.

## Usage

Wrap your OME-Zarr metadata implementation in a CLI program which takes as an argument
a JSON string representing the Zarr attributes of an OME-Zarr dataset (Zarr group).

> Zarr attributes are found in the `"attributes"` key of a Zarr v3 metadata document,
or in the `.zattrs` file for a Zarr v2 group prefix.
>
> In OME-Zarr v0.4, OME-Zarr metadata keys are directly in the top-level attributes,
> so your payload may look like `{"plate": {"version": "0.4", ...}, "well": {"version": "0.4", ...}}`.
> In OME-Zarr v0.5 and above, OME-Zarr metadata keys are beneath an `"ome"` key,
> so your payload should look like `{"ome": {"version": ..., ...}}`.

The program should attempt to parse that string and print to STDOUT another JSON string, with fields

- `"valid"`: boolean, `true` if the payload was compliant with the OME-Zarr specification
- optionally `"message"`: `null` or string with arbitrary further details

The program should not error for invalid OME-Zarr metadata, but may error if malformed JSON is given.

For a program `path/to/my_implementation_wrapper`, call

```sh
python3 ./main.py path/to/my_implementation_wrapper
```

to download all test cases and run them.
Downloads are cached for subsequent runs.

See `./main.py --help` for further information (e.g. restricting to specific OME-Zarr versions).

If your CLI needs additional arguments, wrap the full call in quotes appropriate for your shell, e.g.

```sh
python3 ./main.py --ome-zarr-version 0.5 "path/to/my_implementation_wrapper -version v0.5 -quiet -fromJson"
```

The quoted CLI call will be split according to POSIX rules,
and the JSON payload will be appended as a final argument (effectively in quotes).

Results are returned as a TSV printed to STDOUT.

## TODO

Make this installable, probably as a standalone `uv` script.
