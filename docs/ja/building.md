[English](../building.md)

# ビルド

ここに書いた作業はすべてホスト上で完結し、カメラ、カード、USBデバイスには一切触れません。

## 準備

- Python 3.12。
- LLVM 20（パッケージのビルドに使うアセンブラー`llvm-mc`）。`$KOMA_LLVM`、Homebrewの`llvm@20`（`/opt/homebrew/opt/llvm@20/bin`または`/usr/local/opt/llvm@20/bin`）、`/usr/lib/llvm-20/bin`、`PATH`上の`llvm-mc`の順に探します。
- `bsdtar`。リコーの`gr-240.exe`を直接読む場合にだけ必要です（macOSには`/usr/bin/bsdtar`が入っています。Linuxでは`libarchive-tools`に含まれます）。

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt`は、capstone、numpy、pefile、pillow、pyusb、rawpy、unicornのバージョンを固定しています。任意で、バージョンを固定していないもの：`pytest`（Komaのテストには独自のランナーがあります）、`pyvirtualcam`（`komacam --vcam`）、`opencv-python`（bridgeのテスト1つ）、Chrome付きのPlaywright（ブラウザーのエンドツーエンドテスト。なければスキップ）、`exiftool`、`--real` / `--live`のUSBモードで使うlibusb。

## ファームウェアの入力

ビルドツール（`tools/koma/koma`）のコマンドは、`make-loader`を除き、すべてリコーの2.40ファームウェアを読みます。次の順に選びます。

1. `--firmware PATH`（コマンドの前でも後でも可）。
2. `KOMA_FIRMWARE=PATH`。
3. このチェックアウト内のコピー`firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3`（または`firmware/derived/grd240-armbe.bin`）。存在する場合に使います。

PATHには`qrflect3`、`gr-240.exe`、`qrflect3`を含むzip、または中身だけのペイロード（512バイトのヘッダーを除いた`qrflect3`）を指定できます。どの形式もSHA-256で確認し、それ以外は拒否します。`koma smoke`には、ペイロードだけではなく完全な`qrflect3`が必要です。`firmware/`以下のファイルの出所：[evidence](../internals/evidence.md#firmware-inputs)（英語）。

## ビルドツール

`tools/koma/koma`は、`tools/`をパスに加えて`.venv`から`python -m koma`を実行します。インストールした`koma`コマンドのほうは、Komaアプリを起動します（[インストール](install.md#アプリ)）。下の表の`koma`は`tools/koma/koma`のことです。

| コマンド | 内容 |
|---|---|
| `koma list` | すべての機能のバージョン、概要、変更箇所、設定、オプション。続いて名前付きセット |
| `koma build --features SET -o X.grmod` | パッケージをリンクします。`X.grmod.json`（サイズ、レコード、ハッシュ、バージョン文字列）も書き出します |
| `koma inspect X.grmod` | ファイルをデコードし、すべてのbefore-bytesを2.40およびビルドIDと照合します |
| `koma selfcheck X.grmod` | 静的解析（capstone）とユニットエミュレーターによる、インストール、フック、異常系のチェック。数秒 |
| `koma smoke X.grmod` | ローダーイメージをパッケージ付きでエミュレーター上で1回起動し、1枚撮影。機能ごとにPASS/FAIL。約2分 |
| `koma package-delta STOCK X.grmod -o X.grmod.delta` | パッケージを、Koma独自のバイトと2.40からのコピー指示に符号化します。リリースで配布する形式です。`koma make-package STOCK --delta X.grmod.delta -o GRMOD.BIN`で元に戻します |
| `koma make-loader STOCK -o QRFLECT3` | 1つ目の、カード用ローダーだけのKomaファームウェアイメージをビルドします。SDHCイメージの入力にもなります（[手動インストール](manual-install.md#ファームウェアのビルドと書き込み)を参照） |

カメラ用パッケージの例：

```
tools/koma/koma build --firmware <2.40 file> --features koma04 --opt telemetry.cp15=off -o koma04.grmod
tools/koma/koma inspect koma04.grmod
tools/koma/koma selfcheck koma04.grmod
tools/koma/koma smoke koma04.grmod
```

カメラ用のビルドには、必ず`--opt telemetry.cp15=off`を付けてください（CP15の読み出しで、起動時にカメラが固まったことがあります）。`koma build`の初期値は`cp15=on`です。`--features`には、機能名、名前付きセット、またはその両方をカンマ区切りで指定します。`--opt FEATURE.OPTION=VALUE`は複数回指定でき、その機能がビルドに含まれている必要があります。`--resident pool|static[:ADDR]`は常駐ブロックの置き場所を上書きします（`koma04`の初期値は`0x3dae00`のstatic）。`--source FILE`を付けると、生成したアセンブリも書き出します。

名前付きセット：

| セット | 内容 |
|---|---|
| `koma04` | 現行のセット：メニュー、フォーカス・露出・表示の項目、ルック、SELECT、GRAIN/VIGNETTE、プレビュー、テザー、ライブビュー、ksettings、ピーキング、`waitdiag`（一時的な診断機能）、テザー停止の修正、設定の項目、設定I/O、テレメトリー |
| `koma04-lite` | `koma04`から`ksettings`、`peaking`、`waitdiag`を除いたもの |
| `koma03`、`koma03io`、`koma03sio`、`koma03lv` | 以前のセット（ルック、GRAIN/VIGNETTE、プレビュー）と、その設定I/O版、ライブビュー版 |
| `koma02`、`koma01` | より小さな、以前のセット |
| `drop1`..`drop3`、`probe` | 初期のドロップと、カラーレジスタの調査用 |

`--features`を指定しない場合、`build`は古い3機能の初期セット（`tone-r3,snap15,recipe-slots`）を使います。`koma04w`（`koma04`にSDHC・SDXC用の`widefs`を加えたもの）は、`analysis/sdhc/bundle/build.py`でビルドします。`widefs`は登録された機能ではないためです（[手動インストール](manual-install.md#komaのインストール)）。

`smoke`は、パッケージを`GRMOD.BIN`としてローダーイメージを起動し、モデル化したメモリーを純正のメニューからフォーマットし、1枚撮影して現像のあとで止まります。機能ごとに、ローダーがパッケージを呼んだこと、インストールのステータスが1であること、すべてのレコードがRAMにあること、すべてのフックが実行されたこと、機能自身のチェックが通ることを確認します。撮影で到達しないフックはSKIPと表示します。それらはユニットテストで確認します。smokeの合格は`EMU`の証拠にしかなりません。

## パッケージの形式

コンテナ、4種類のレコード、インストールの流れは[仕組み](how-it-works.md#grmodbin)にあります。常駐コードはメモリーに残ります。インストーラー、レコード、再配置情報、`transient=True`の機能は、インストール後に解放されます。ローダーは本体全体を64KiBまでに制限します。

## 機能の書き方

機能は`tools/koma/features/<name>.py`に置きます。`NAME`、`SUMMARY`、`OPTIONS`（オプション名からヘルプ文への対応）、任意の`ROWS`、`Feature`を返す`make(opts)`を定義します。`tools/koma/features/__init__.py`の`REGISTRY`に登録します。

```python
from ..feature import Check, Feature, Hook

NAME, SUMMARY, OPTIONS = "my-hook", "counts stills", {}
ROWS = [dict(id="count", label="COUNT", options=["OFF", "ON"], byte=0x1b8ce, default=0, group="display")]

SRC = """
handler:
    push {r4, lr}
    adr r1, count
    ldr r2, [r1, #0]
    adds r2, #1
    str r2, [r1, #0]
    ldr r3, =next
    bl call_r3                 @ original 0x8ff0e, or the next feature's handler
    pop {r4}
    pop {r3}
    bx r3
call_r3:
    bx r3
.ltorg
.p2align 2
count: .word 0
"""

def make(opts):
    return Feature(NAME, "1.0", SUMMARY, source=SRC, options=opts,
                   hooks=[Hook(0x96436, 0x8FF0E, "handler", "next", "still develop")],
                   checks=[Check(0x8FF0E, bytes.fromhex("b5884929"), "callee prologue")])
```

この例は、ユニットエミュレーターで問題なくリンク、インストールできます。

- ソースはllvm-mcの構文で書くThumbコードです（ARMv4T、`arm7tdmi`、ビッグエンディアン）。位置独立にします。ラベルは機能ごとに閉じています。ほかの機能がエクスポートしたシンボルは`$other.symbol`で参照し（`requires`に記載）、コアのシンボルは`$koma.version`のように参照します。ファームウェアの関数は`ldr r3, =0xADDR+1`のあと`bl call_r3`で呼びます。絶対アドレスへの`bl`は使いません。CP15命令は絶対に使わないでください。
- フックのハンドラーは、元の呼び出し先と同じ状態（同じr0-r3、スタック、lr）で呼ばれます。元の引数で`next`を呼び、r4-r11を保存します。
- パッチはbefore-bytesを持ちます：`Patch(addr, before, after, why)`。`Check(addr, before, why)`は、コードが依存するバイトを確認するだけで、何も書き込みません。
- メニュー項目のバイトは`0x1b8b5..0x1b8cf`の範囲内で、ほかの機能が使っていないものにします。項目がそのバイトを確保するので、`Settings`として重ねて宣言しないでください。ラベルは16文字まで（推奨14文字）、選択肢は9文字までで、純正フォントの文字（A-Z、0-9、スペース、`!&'()+-./:*`）だけを使います。`default`は`options`のインデックスです。`group`は`recipe`、`recipe-edit`、`focus`、`exposure`、`display`のいずれかで、それ以外はOTHERページに入ります。バイトは使う時点で読み、範囲外なら初期値として扱います。
- それ以外の設定バイトでは、`Settings(start, end, owned=True)`で専用の保存領域を示します。純正のフィールドに書き込むには、`owned=False`と`values`（純正自身が書き込む値の集合）が必要です。
- smokeテスト用に、撮影前に行う書き込みを`{address: byte}`で返すモジュールレベルの`smoke_setup()`と、`[(name, ok, detail)]`を返す`smoke_checks(obs)`を任意で追加できます。後者は`Feature(..., smoke=smoke_checks)`として渡します。

[ビルド時のlint](how-it-works.md#ビルド時のlint)のルールに反するビルドは、リンカーが拒否します。

`tools/koma/data/`には、機能が読む表があります。兄弟機種のリコーのファームウェアから数値として書き写したモノクロのトーン表と、GR DIGITAL IVのファームウェアの表から導いたルックのパラメーターです。

### 機能のユニットテスト

`tools/koma/unitemu.py`は、ファームウェアの呼び出し先をスタブにして、2.40のペイロード上で本物のインストーラーをUnicornで動かします。

```python
from koma.linker import link
from koma.unitemu import Unit

u = Unit(link([my_hook.make({})]).module)
assert u.install() == 0 and u.callee_saved_ok
u.stub(0x8FF0E, "original")
```

`u.read(addr, n)`でパッチ後のバイトを確認し、`u.run_site(start, end, regs)`でフック周辺の純正のコードを実行します。テストは`tools/koma/tests/test_<name>.py`に置きます。

## テストの実行

```
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/tests/run_tests.py [-k text]
```

Komaのテストスイート（約445件、約3.5分）は、pytestがあればpytestを、なければ独自の最小限のランナーを使います。クローンした直後の状態では、追跡されていないファイルを必要とする出所確認のテストが2件失敗します。

リポジトリ全体のチェックは1つの入口から実行します。`PYTHONPATH`はこの入口が設定します。

```
.venv/bin/python tools/check.py list         # how every test file is classified
.venv/bin/python tools/check.py test         # fast suite
.venv/bin/python tools/check.py test-all     # everything, including slow tests that regenerate fixtures
.venv/bin/python tools/check.py verify       # read-only checks of retained evidence (see evidence)
.venv/bin/python tools/check.py playground   # browser playground of the emulated camera UI
```

`test`は、高速なunittestとスクリプトのグループを実行し、追跡対象のファイルには何も書き込みません。`test-all`は低速なグループを加えます。`run_tests.py`によるKomaのテストスイート、`pytest`によるkomacamのテスト（先にインストールしておきます）、`analysis/verify-fixtures/`以下のフィクスチャーを書き直すテスト（何も変わっていなければバイト単位で同一）です。

一部のチェックには、gitに含まれていない証拠（`analysis/camera-dumps/`、ネイティブの検証レポート）が必要です。副ワークツリーでは、`check.py`が参照用チェックアウト（`REVERSE_REFERENCE_ROOT`、なければ`git worktree list`の最初のエントリー）から読みます。単純なクローンでは、これらの手順は実行できません。[evidence](../internals/evidence.md)（英語）を参照してください。

## そのほかのツール

`tools/`以下の研究用ツール（エミュレーター、UIプレイグラウンド、パッチビルダー、カードスクリプトのリンター、ルックの試作）には、それぞれのフォルダーにREADMEがあります。概要は[internals 10](../internals/10-emulator-and-tooling.md)（英語）にあります。カメラと通信するホストツールは[ホストツール](host-tools.md)にあります。

カードのバッテリーログは`python3 tools/koma_battlog_decode.py KOMABATT.TXT`でデコードします。
