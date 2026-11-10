# Contributing

Create a Python environment and install dependencies:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The app needs Python 3.10 or newer. Node is needed to build its interface. Building firmware features needs the toolchain in [building](docs/building.md).

Run tests with:

```sh
python tools/check.py test
```

When reporting a camera result, include the camera model, firmware tag from the menu, steps, expected result and actual result. Never commit camera serial numbers or personal data.

Koma is developed with AI assistance; every change is reviewed and hardware results are tested by the maintainer.
