# Host tools

Run `koma` to open the Koma app. Use `koma --tray` for the menu bar or system tray, and `koma doctor` to check USB. See [installation](install.md).

Set SETUP > USB CONNECTION to ORIGINAL and turn the camera on before connecting USB.

## For developers

Commands below run from a checkout configured as described in [building](building.md).

`komabridge` serves the app and connects to the camera. For a simulated camera:

```sh
.venv/bin/python tools/komaset/bridge/komabridge.py --mock --mock-tether --mock-hw
```

Use `--real` for a camera. See the [bridge README](../tools/komaset/bridge/README.md).

`komacam` serves a webcam stream. Its `--spawn real` option starts the bridge. In OBS, add a Browser source at `http://127.0.0.1:8770/`, then start OBS Virtual Camera. See the [komacam README](../tools/komacam/README.md).

`komaset` reads and writes settings files; `grdusb` provides USB commands. See their [settings](../tools/komaset/README.md) and [USB](../tools/grdusb/README.md) documentation.
