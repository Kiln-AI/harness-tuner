# Releasing Kiln Harness Tuner

This guide publishes a new version of the `harness-tuner` package to
[PyPI](https://pypi.org/project/harness-tuner/). You need `uv`, `git`, and a
PyPI token with publish access to the project.

The commands below use a shell variable for the version. Set it first, in the
shell where you run all the steps:

```
VERSION=0.7.0
```

## 1. Bump the version

1. Change `version` in `pyproject.toml` to the new version.
2. Update the lock file:

   ```
   uv lock
   ```

3. Commit `pyproject.toml` and `uv.lock`, and push them. CI must
   pass on that commit before you continue.

## 2. Make the build

1. Remove the old build and make a new one:

   ```
   rm -rf dist && uv build
   ```

2. Check the sdist and the wheel. The file names must have the new version:

   ```
   uvx twine check dist/harness_tuner-$VERSION*
   ```

3. Test the installed wheel from an empty folder:

   ```
   uv run --no-project --python 3.13 tests/packaging/smoke_installed_wheel.py --dist dist
   ```

   Each passed check prints one `ok:` line. Do not release if a check fails.
   [CONTRIBUTING.md](CONTRIBUTING.md) tells what this test does.

## 3. Release

1. Publish to PyPI:

   ```
   uv publish dist/harness_tuner-$VERSION*
   ```

2. Tag the release commit, and push the tag:

   ```
   git tag -a v$VERSION -m "v$VERSION"
   git push origin v$VERSION
   ```

3. Make sure that the new version is on
   [PyPI](https://pypi.org/project/harness-tuner/).

4. Install the published package, and start it:

   ```
   uv tool install --force harness-tuner
   harness-tuner
   ```

   The start-up screen shows the version. Make sure that it is the new
   version. If it shows the old version, PyPI can be slow to update its index.
   Wait a minute, then install again.
