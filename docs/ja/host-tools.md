[English](../host-tools.md)

# ホストツール

`koma`を実行するとKomaアプリが開きます。メニューバーやシステムトレイに常駐させるには`koma --tray`、USBを確認するには`koma doctor`を使います。[インストール](install.md)を参照してください。

USBをつなぐ前に、SETUP > USB CONNECTION（USB接続）をORIGINALにして、カメラの電源を入れておきます。

## 開発者向け

以下のコマンドは、[ビルド](building.md)の手順で設定したチェックアウトで実行します。

`komabridge`はアプリを提供し、カメラと接続します。カメラをシミュレートする場合：

```sh
.venv/bin/python tools/komaset/bridge/komabridge.py --mock --mock-tether --mock-hw
```

実機のカメラには`--real`を使います。[bridgeのREADME](../../tools/komaset/bridge/README.md)（英語）を参照してください。

`komacam`はWebカメラ用のストリームを配信します。`--spawn real`オプションを付けるとbridgeも起動します。OBSで`http://127.0.0.1:8770/`をブラウザソースとして追加し、OBS Virtual Cameraを開始します。[komacamのREADME](../../tools/komacam/README.md)（英語）を参照してください。

`komaset`は設定ファイルを読み書きし、`grdusb`はUSBコマンドを提供します。それぞれの[設定](../../tools/komaset/README.md)と[USB](../../tools/grdusb/README.md)のドキュメント（英語）を参照してください。
