# Koma launcher

The launcher starts the bridge, opens the app and manages the tray and USB checks.

```sh
PYTHONPATH=tools/komalauncher python3 -m komalauncher --mock
PYTHONPATH=tools/komalauncher python3 -m komalauncher
```

Build the app first with `cd tools/komaapp && npm ci && npm run build`. The real camera path needs pyusb and libusb.

Run launcher tests with `python3 -m pytest tools/komalauncher/test_komalauncher.py -v`. Build the package with `python3 packaging/koma_build.py`. See [building](../../docs/building.md).
