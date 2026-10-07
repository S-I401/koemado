[English](README.en.md) | 日本語

# koemado（声の窓）

Windows の PC で、**ワンクリックで導入できるローカル音声会話アバター**です。\
マイク（またはテキスト）で話しかけると、
ローカルの LLM が返事を考え、Irodori-TTS が日本語で読み上げます。\
クラウドへの送信はありません（外部の推論サーバーを自分で選んだ場合を除く）。

```
ブラウザ（マイク） ─ AIAvatarKit ─ STT: faster-whisper（CPU）
                         │
                         ├─ LLM: llama.cpp の llama-server（GGUF）  ※ 外部の推論サーバーに替えることもできます
                         └─ TTS: Irodori-TTS v4.1 Small（int8, CUDA）
```

> **成人キャラクターのみ**です。\
> このプロジェクトのキャラクター設定では、年齢（18 歳以上）が必須で、未成年、または未成年を示す設定（「少女」「中学生」など）は保存できません。\
> 同梱のサンプルキャラクター「ミオ」は 28 歳の成人で、声も Voice Design で作った成人女性の声（実在の人物の声ではない）です。

## 必要環境

| 項目 | 内容 |
|---|---|
| OS | Windows 10 / 11（64bit） |
| GPU | **NVIDIA GPU 必須**（RTX 20 シリーズ以降、VRAM 6GB 以上）。<br>ドライバ **570 以上**（PyTorch CUDA 12.8 の要件） |
| メモリ | 16GB 以上（標準プロファイルは 32GB 以上） |
| 空き容量 | プロファイルにより約 19〜35GB（下の表）。<br>インストール先のドライブに必要です |
| ネットワーク | 導入時に GitHub / Hugging Face / PyPI からダウンロードします（合計 約 15〜32GB） |

NVIDIA GPU が無い PC では、TTS が 1 文あたり十数秒かかり会話にならないため、導入を中止します。

## 導入

1. このリポジトリをダウンロード（ZIP を展開、または `git clone`）します。\
   **空き容量の大きいドライブ**に置くのがおすすめです（インストール先は既定でこのフォルダです）。
2. `setup.bat` をダブルクリックします。
   - PC のスペック（GPU・VRAM・メモリ・空き容量・使用中のポート）を調べ、合うプロファイルを選んで、内容を表示します。\
     **Enter** で導入を始めます。
   - uv（Python の管理ツール）が無ければ、リポジトリ内の `tools\` に自動で入れます。\
     システム全体には何も入れません。
   - 途中で止まっても、もう一度 `setup.bat` を実行すれば続きから再開します（ダウンロード済みのものは取り直しません）。
3. 導入が終わったら `start.bat`（またはデスクトップのショートカット）をダブルクリックします。\
   **黒い画面は出ません。**\
   ブラウザに「起動中…」の画面（管理画面）が開き、LLM → TTS → STT → AIAvatarKit の順に起動して、全部の準備ができたら自動で会話画面に切り替わります。
4. 止めるときは、タスクトレイの koemado アイコン →「終了」、管理画面の「終了」ボタン、または `stop.bat` です。

### 導入前の確認

`setup.bat` は、検出結果と導入内容の表（項目ごとのダウンロード量・ディスク使用量、合計、インストール先の空き容量と導入後の残り、100Mbps / 1Gbps での概算時間）を出し、
**`この内容でインストールしますか？ [y/N]:` に `y` または `yes` と答えたときだけ**始めます。\
それ以外（空の Enter、`n` など）は何も変更せずに終了します。\
`--yes` では確認を省きますが、表は必ず表示します。\
途中から再開するときは、取得済みの分を差し引いた「残りの量」を表示します。

### Strata を使う（VRAM 12GB・RAM 48GB 以上の PC で推奨）

**VRAM 12GB 以上かつ RAM 48GB 以上**で、ディスクに Strata の分（約 80GB）の空きもあるときは、プロファイル（high / mid / low）に関係なく、
setup が [Strata](https://github.com/Niko1221/Strata)（Qwen3.8-Flash-Next）を案内します。\
同梱の LLM（mid なら Gemma 4 26B-A4B）より返答の質が大幅に上がります。\
VRAM が 12〜19GB の PC では、専門家のキャッシュが小さくなるぶん速度は下がります（Strata の README の実測: RTX 5070 12GB・RAM 64GB で IQ2_XS 約 79 tokens/s、IQ3_S 約 53 tokens/s）。

- Strata の要件: NVIDIA（または Strata 対応の AMD。ただしこのプロジェクト自体は NVIDIA 専用）で VRAM 12GB 以上、RAM 48GB 以上（RAM 32GB では Coder 版しか載らず、日本語の雑談に弱いので案内しません）、ディスク約 80GB（ダウンロード約 70GB）。
- 注意: 別のソフトウェアです（ライセンスは Strata 側）。\
  1 リクエストずつしか処理しません。\
  起動時に 1〜3 分 PC が重くなります。
- 選択肢: **[1]** Strata を導入して使う（`<インストール先>\strata` に取得し、Strata のインストーラーを別ウィンドウで起動。モデルの選択とダウンロードはそちらの画面）／ **[2]** すでに入っている（URL を入力）／ **[3]** 同梱の LLM（選ばれているプロファイル名つき）で続ける。
- `--yes` では約 70GB を勝手に入れません（選ばれた同梱プロファイルで続け、案内だけ表示）。\
  `--llm strata` で [1]、`--llm external --llm-url http://127.0.0.1:8080/v1` で [2] を選べます。
- [1] のあとは、`start.bat` が Strata を先に起動し、`/v1/models` が返るまで最大 10 分待ちます（管理画面に進捗を表示）。\
  ポートは Strata の `strata-*.json` から読みます。\
  `stop.bat` は、`start.bat` が起動した Strata だけを止めます。
- Strata が VRAM を専門家のキャッシュで埋めてしまわないよう、**この導入先の Strata の設定にだけ** `--vram-reserve-mib 3500`（TTS 用の余白）を入れます。\
  LLM→TTS の直列化はオン、`reasoning_effort=none`、temperature 0.8 です。
- 取得するコミットは固定しています（`82f46a8`）。\
  Strata の更新は、`strata\UPDATE.bat` を自分で実行してください。
- Strata のインストーラー（START-HERE.bat）は、Python が無い PC では Python 3.12 をユーザー領域に入れることがあります（Strata 側の挙動）。

### Strata の拒否の抑制（ESP）と画像認識

設定画面の LLM タブ（Strata のとき）と `configure.bat` で切り替えます。

- **拒否の抑制（Strata の experimental speed projection）**: 既定はオフ。\
  **名前と違って高速化ではありません。**\
  Strata 同梱の制御ベクトルで「拒否の方向」を取り除くため、**モデルが依頼をほとんど断らなくなります（安全上の振る舞いが外れます）**。\
  速度は 1 トークンあたり 0.2〜0.4% 遅くなる程度です。\
  出力の責任は利用者にあります。\
  ベクトルは Qwen3.8-Flash-Next 専用（Swift 1.5・Coder 版では使えません）で、ライセンスは Qwen Community License 1.0 です。\
  オンにするときは確認ダイアログが出ます。\
  キャラクターは成人のみ、という制限は ESP の有無に関係なく有効です。
  - **start.bat が起動する Strata（`strata\`）**: オンにすると `strata-<モデル>-esp.json`（Strata のセットアップと同じ形。ログ名も `-esp.log`）を作ってそれで起動します。\
    オフなら元の設定で起動します。\
    変更後は Strata の再起動が必要です（画面に再起動ボタンを出します）。
  - **自分で起動した Strata**: 起動の引数は変えられないので、リクエストの `experimental_speed_projection`（オンなら `true`、オフなら `false`）だけを送ります。\
    ベクトルを読み込んで起動した Strata でのみ効きます。\
    効いているかどうかは `/props` などに出ないため判別できません。
- **画像認識（vision）**: `setup.bat` の Strata の経路で「GPU（推奨。1 枚 0.1〜0.5 秒、VRAM 約 1.4GB）／CPU（1 枚 10〜30 秒）／使わない（既定）」を選べます（`--vision gpu|cpu|no`。Strata のインストーラーにも渡します）。\
  GPU のときは、TTS 用の余白 3500MiB にエンコーダの 1400MiB を足した 4900MiB を `--vram-reserve-mib` に設定します（Strata のセットアップは GPU でも CPU でも 700 を書くだけで、エンコーダの分を自動では上乗せしないため）。
  - 設定画面で画像認識をオンにすると、会話画面に「画像を送る」パネル（ファイル選択・貼り付け・カメラ撮影）が出ます。\
    画像はこの PC の中だけで扱い、**会話の記録（SQLite）にも保存しません**（設定画面でオンにすると残せます）。
  - 外部のサーバーでは、サーバー側で画像認識が有効な場合のみ使えます。\
    接続テストで小さな画像を送って確認できます。
- 今後の拡張: 同梱の LLM（Gemma 4 / Qwen3.6）も mmproj を付ければ画像に対応できますが、今回は対象外です。

### setup.bat のオプション

```
setup.bat --dry-run                       調べて計画を表示するだけ（何も入れない）
setup.bat --profile mid --yes             プロファイルを指定し、確認なしで進める
setup.bat --install-dir D:\aiavatar       インストール先を指定（既定: このフォルダ）
setup.bat --llm external --llm-url http://127.0.0.1:1234/v1 --llm-model モデル名
                                          外部の推論サーバーを使う（llama.cpp とモデルは入れない）
setup.bat --llm strata                    Strata を導入して使う（high の PC 向け。約 70GB）
setup.bat --dry-run --simulate "vram=8,ram=32,disk=60"   別の PC を想定した判定（テスト用）
```

## プロファイル

LLM は llama.cpp の `llama-server` で動かします。\
PC のスペックから自動で選びます（`--profile` で変更可）。\
空き容量が足りないときは、1 段下のプロファイルに下げます。

| プロファイル | 条件（VRAM / メモリ） | LLM（GGUF） | モデルのサイズ | 必要な空き容量（目安） |
|---|---|---|---|---|
| high | 20GB 以上 / 48GB 以上 | Qwen3.6-35B-A3B（UD-Q4_K_M） | 20.6 GiB | 約 35GB |
| mid（標準） | 8GB 以上 / 32GB 以上 | Gemma 4 26B-A4B（QAT UD-Q4_K_XL） | 13.3 GiB | 約 27GB |
| low | 6GB 以上 / 16GB 以上 | Gemma 4 E4B（QAT q4_0） | 4.8 GiB | 約 19GB |

- **mid** は MoE モデルの専門家（experts）の重みの大部分を CPU 側に置き、VRAM を LLM 約 3.6GB + TTS 約 2.7GB（ピーク）に収めます（VRAM 8GB の GPU で、Windows のデスクトップ分を残すため）。
- 容量の内訳: llama.cpp 約 1.2GB、TTS の実行環境（PyTorch CUDA）約 5.2GB、TTS・STT のモデル約 2GB、その他の環境約 1GB（ダウンロードのキャッシュは導入の最後に消します）。
- モデルの差し替えは `profiles.py` の表を直します。\
  Hugging Face のトークンを登録すると、設定画面で GGUF の一覧から、プロファイルの範囲内で自由に選べます（「モデルブラウザ」の節）。

### 固定しているバージョン

llama.cpp `b11443`（CUDA 12 ビルド）／ Irodori-TTS-Server `61012c7`（`uv.lock` に従う）／ Irodori-TTS v4.1 Small int8（`Aratako/Irodori-TTS-v4.1-Small-Quantized`）／ aiavatar 0.9.1 ／ fastmcp 4.0.11 ／ ddgs 9.16.0 ／ faster-whisper 1.2.1（small, CPU int8）／ pystray 0.19.5 ／ pillow 12.3.0。\
依存のバージョンは `app/requirements.txt` と `stt/requirements.txt` で固定しています。

## 起動と管理画面

`start.bat` は、ウィンドウを出さない常駐の管理プロセス（`supervisor.py`）を起動して、すぐに閉じます。\
管理プロセスが LLM（llama-server、または Strata）・TTS・STT・AIAvatarKit を**ウィンドウなし**で起動して見守ります（技術に明るくない人が、黒い画面がたくさん開いて驚かないように）。

- **管理画面**: `http://127.0.0.1:8099/`（使用中なら空きポートを選び、`config.json` に保存。AIAvatarKit が落ちていても開けます。127.0.0.1 からだけ、Host / Origin も検査）。
  - 「状態」: サービスごとの稼働状況・ポート・起動からの時間、VRAM と RAM、各サービスの再起動、全体の再起動・終了。\
    起動中は「起動中…」と進捗を出し、準備ができたら自動で会話画面へ移ります（初回の Strata の長い読み込みもここで見られます）。\
    起動に失敗したときは、失敗したサービスのログを開くボタンが出ます。
  - 「ログ」: サービスごとのタブと「すべて」。\
    自動で更新（SSE）、INFO / WARNING / ERROR の色分けと絞り込み、文字列検索、自動スクロールの一時停止、コピー、ダウンロード。
  - **「不具合報告用にまとめてダウンロード」**: 直近のログ全部と環境情報（GPU・ドライバ・メモリ・プロファイル・バージョン）を 1 つの zip にします。\
    **友人からのデバッグ報告は、これを送ってもらってください。**
  ![状態](docs/screenshots/console_status.png)
  ![ログ](docs/screenshots/console_logs.png)
- ログは `logs\<サービス>.log` にも保存します（5MB でローテーション、4 世代）。\
  **API キー・トークンらしい文字列（設定画面で入力したキーそのもの、`Bearer` の値、`api_key=` など）は伏せ字**にしてから保存・表示・ダウンロードします。
- **異常終了したサービスは、自動で再起動します**（5 分に 3 回まで）。\
  それ以上は止めて、管理画面に「〇〇が起動できません。ログを確認してください」と出します。
- 子プロセスは Job Object で管理プロセスに結び付けています。\
  管理プロセスが強制終了されても、OS がサービスをすべて止めます（孤児が残りません）。\
  二重起動はしません（動いているときの `start.bat` は、画面を開くだけです）。
- **タスクトレイ**にアイコンが出ます（メニュー: 会話画面を開く / 設定 / ログ・状態 / 再起動 / 終了）。\
  トレイが使えない環境でも、本体は動き続けます（管理画面の「終了」と `stop.bat` で止められます）。
- 会話画面と設定画面の右上に、管理画面へのリンクがあります。
- `setup.bat` の最後に、デスクトップとスタートメニューに「koemado」のショートカットを作るか聞きます（ウィンドウを出さずに起動）。\
  あとからは `app\.venv\Scripts\python.exe ctl.py shortcuts` で作れます。

## 使い方

- 会話画面: `http://127.0.0.1:8100/static/index.html`（管理画面の「起動中…」が自動で移ります）。\
  マイクの許可が必要です。
- **設定画面: `http://127.0.0.1:8100/settings`**（声・LLM・キャラクター・ツール。`configure.bat` でも同じ設定を対話式に変えられます）
- 管理画面（計測・ログ）: `http://127.0.0.1:8100/admin`
- ブラウザを使わずに試す: `app\.venv\Scripts\python.exe tools\e2e_test.py "こんにちは" --repeat 3`（WebSocket でテキストを送り、返答・表情タグ・音声・所要時間を表示）

すべてのサーバーは `127.0.0.1` だけで待ち受けます。\
設定画面も `/ws` も、他の PC やほかのサイトからは使えません（Host / Origin も検査します）。

### 設定画面でできること

![声](docs/screenshots/settings_voice.png)

**声**: 参照音声（`tts/voices/`）の一覧と試聴、切り替え（**次の発話から反映。再起動不要**）。\
参照音声のアップロード（wav / flac / mp3 / ogg / opus。m4a / aac / webm はこの環境で読めないので不可）。\
Voice Design（キャプション + seed で候補を作り、試聴して気に入ったら保存）。\
num_steps（速度と音質）。\
アップロード画面には「本人の同意がない実在人物の声は使わない」注意と同意の確認を出します。

![LLM](docs/screenshots/settings_llm.png)

**LLM**:
- **同梱の llama-server**: プロファイル（high / mid / low）の選択、または**任意の GGUF**（Hugging Face のリポジトリ + ファイル名、またはローカルの .gguf のパス）。\
  選んだモデルのサイズを、この PC のスペック（VRAM / メモリ / 空き容量）と比べて「重すぎる」「容量が足りない」を警告します。\
  ダウンロードは確認のあとで、進捗バーを出します。\
  temperature、思考の有無、コンテキスト長も設定できます。
- **外部の推論サーバー**（OpenAI 互換）: Strata / LM Studio / Ollama / vLLM / 別 PC の llama-server / クラウド API。\
  base_url、API キー（任意）、モデル名（`/v1/models` から選択）、思考を止める方法（`reasoning_effort` / `chat_template_kwargs` / 送らない）、ツール呼び出しの有無、LLM→TTS の直列化のオン／オフを指定します。\
  **接続テスト**ボタンで、応答時間・思考の混入・ツール呼び出しを確認できます。
  - 直列化: 同じ GPU で LLM と TTS を動かす場合は**オン**（VRAM と演算の取り合いを防ぎ、最初の音声が早くなります）。\
    別 PC やクラウドならオフが目安です。
  - 外部サーバーは `start.bat` では起動しません。\
    先に起動しておくと、`start.bat` が `/v1/models` が返るまで最大 2 分待ってから AIAvatarKit を起動します。
  - プリセット: Strata（`:8080/v1`。1 リクエストずつしか処理しません。VRAM 12GB・RAM 48GB 以上が必要。推奨の条件は上の「Strata を使う」。拒否の抑制（ESP）と画像認識は下の節）、LM Studio（`:1234`）、Ollama（`:11434/v1`）、vLLM、別 PC、クラウド。\
    ポートは各ツールの既定値で、変更できます。
  - API キーは `config/settings.json` に保存します（`.gitignore` 済み）。\
    画面には再表示せず（伏せ字）、ログにも出しません。
  - クラウド API（または別ネットワーク）を選ぶと、**会話の内容が外部に送られる**ことを画面に表示します。
- LLM の方式・モデル・接続先・コンテキスト長を変えたら、画面の「保存して再起動」（または `start.bat` の再実行）で反映します。\
  temperature と思考の設定は次のメッセージから反映されます。

#### モデルブラウザ（Hugging Face のトークンを登録して、LLM を自由に選ぶ）

Hugging Face（HF）のトークンを登録すると、HF の GGUF モデルの一覧から、**プロファイルに合った範囲で**自由に LLM を選べます。\
トークンは任意です（無くても、プロファイルの表のモデルと、「任意の GGUF」の手入力は使えます）。

- **トークン**: LLM タブ（と `configure.bat` の「Hugging Face のトークン」）で登録・確認・削除します。\
  作り方は、[HF の設定ページ](https://huggingface.co/settings/tokens)で「Create new token」を選び、種類は **Read（読み取り）** で足ります。
  - 保存先は `config/settings.json`（`.gitignore` 済み）です。\
    画面には伏せ字で出し、保存後は再表示しません。
  - 「確認」ボタンは `https://huggingface.co/api/whoami-v2` だけを呼び、ユーザー名と有効かどうかを表示します（無効なトークンは「無効」と出ます）。
  - **トークンの送り先は huggingface.co だけ**です。\
    ダウンロードがリダイレクトで CDN に移るときは、トークンを外してから続けます。
  - 使う場所は、検索、ファイル一覧、ダウンロード（ゲート付きモデルを含む）です。\
    `setup.bat` も、環境変数 `HF_TOKEN`、または設定済みのトークンがあれば使います。
  - 管理画面のログ、不具合報告の zip、設定 API の応答には、トークンの値を出しません（`hf_` で始まる文字列も伏せ字にします）。
  - 未登録のときは、一覧からの選択はできません。\
    「トークンを登録すると一覧から選べます」と表示します。
- **検索**: 検索語、並び順（ダウンロード数・いいね数・更新日）、「日本語に強いと言われる系列だけ」（Qwen、Gemma、Swallow、ELYZA など。一覧は `profiles.py` の `JA_FAMILIES`）、ページ送り。
- **ファイルの一覧**: リポジトリを選ぶと、量子化の種類とサイズを出します。\
  分割された GGUF（`-00001-of-0000N`）は 1 つのモデルとして合計サイズで出し、mmproj などの補助ファイルは候補から外します。\
  各候補と各リポジトリに、ダウンロード数・いいね数・更新日・ライセンス・ゲートの有無を表示します。
  - ゲート付きで利用条件に同意していないときは、「HF のページで利用条件に同意してください」とリンクを出します（ダウンロードの 403 でも同じ案内を出します）。
- **範囲（下限と上限）**: プロファイルごとに、選べるファイルサイズの範囲を決めています（`profiles.py` の `MODEL_LIMITS`）。\
  範囲外のファイルは灰色で表示し、理由を添えて選べなくします。
  - 範囲は、現在のプロファイルとあわせて画面に表示します。\
    プロファイルを変えると、範囲も変わります。
  - 範囲内でも、次の場合は注意を出します。\
    dense（MoE ではない）モデルで、TTS の余白を除いた VRAM を大きく超えるときは、CPU に回る分が多く遅くなります。\
    MoE のときは、`--n-cpu-moe` を mid と同じ方式で計算できるかを表示します。
  - 種類（dense / MoE）と層数は、GGUF の先頭を読んでメタデータから調べます。\
    読めないときは、名前（`35B-A3B` など）から推定します。
- **ダウンロードと切り替え**: サイズと空き容量を表示して確認を取り、ダウンロードします（進捗、途中からの再開、キャンセル）。\
  終わったら、そのモデルで llama-server を再起動し、接続テスト（短い応答、思考が混ざらないか、ツール呼び出し）を自動で行います。\
  失敗したときは「元のモデルに戻す」ボタンを出します。
  - llama-server の引数は、既存のプロファイルと同じ方式で決めます。\
    MoE で層数が分かり、導入時のプロファイルが mid のときは、専門家を CPU に置く層数（`--n-cpu-moe`）を VRAM から計算します。\
    それ以外は `--fit` に任せます。\
    いずれも `--jinja` を付けます。\
    コンテキスト長は 8192 です（このPCの上限に近い大きいモデルは 4096）。
  - ダウンロード済みのモデルの一覧と削除（容量の回復）もあります。\
    保存先は `models\<owner>--<name>\` です。

| プロファイル | 選べるサイズの範囲 | 注意を出す閾値 | 範囲の根拠 |
|---|---|---|---|
| high | 8〜28 GiB | 25 GiB 以上 | 既定（20.6 GiB）の約 1.35 倍まで。<br>VRAM 24GB + RAM 48GB で載る範囲です。<br>下限は約 12B の Q4 です |
| mid | 4〜16 GiB | 14 GiB 以上 | 既定（13.3 GiB）の約 1.2 倍まで。<br>RAM 32GB の約半分です。<br>下限は約 7B の Q4 です |
| low | 2〜6 GiB | 5.4 GiB 以上 | 既定（4.8 GiB）の約 1.25 倍まで。<br>RAM 16GB から OS・TTS・STT を引いた残りです。<br>下限は約 3B の Q4 です |

下限より小さいモデルは、日本語の会話やツール呼び出しが不安定になりやすいため、選べません。

上の範囲に加えて、検出したハードからの絶対的な上限があります。\
**（RAM − OS 4GiB・STT 1.5GiB・アプリ 1.5GiB・TTS 3GiB）+（VRAM − TTS 2.7GiB・デスクトップ 1.5GiB）− KV キャッシュと計算バッファ 1GiB** と、インストール先の空き容量（余裕 1GiB を残す）の小さい方です。\
たとえば VRAM 8GB・RAM 32GB の PC では、ハードからの上限は約 24.8 GiB です（mid の範囲 16 GiB のほうが小さいので、mid ではそちらが効きます）。

![キャラクター](docs/screenshots/settings_char.png)

**キャラクター**: 1 キャラクター = 1 フォルダ（`characters/<id>/character.yaml` と `image.png`）。\
名前・年齢（必須）・一人称・二人称・口調・性格・好き嫌い・生い立ち・関係性・話し方の例文・使う声を設定し、複数のキャラクターを切り替えられます。
- `character.yaml` から system_prompt を組み立てます（話し方・表情とモーションのタグ・天気・記憶のルールは `characters/_common.md` に共通で入ります）。\
  生い立ちなどの長文は会話中ずっと同じ文字列なので、LLM のプロンプトキャッシュが効きます。
- 立ち絵: 画像は設定画面からアップロード（PNG）し、会話画面に表示します。\
  `image_joy.png` のような表情別の画像（neutral / joy / angry / sorrow / fun / surprised / eyes_closed）があれば表情に応じて切り替わります。\
  画像が無いときはシルエットです。
- 記憶（`app/data/memory_<id>.db`）はキャラクターごとに分かれます。
- **成人のみ**: 年齢が 18 歳未満、または文章に未成年の年齢（17 歳など）や未成年を示す語（少女・中学生・高校生など）が含まれると保存できません。

設定の保存先は `config/settings.json`（声・LLM）と `characters/`（キャラクター）です。\
`.env`（`app/.env`）はポートなど低レベルの値だけで、`start.bat` が作り直します。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| 「ドライバが古すぎます」で止まる | NVIDIA の最新ドライバに更新してください（570 以上）。<br>このスクリプトはドライバを更新しません |
| 導入に時間がかかる | 初回は合計 15〜32GB のダウンロードと、PyTorch（CUDA）の展開があります。<br>HDD や、ウイルス対策ソフトのリアルタイム検査が有効だと、展開に数十分かかることがあります（SSD なら数分）。<br>途中で止めても再実行で続きから再開します |
| 「空き容量が足りません」 | `--install-dir` で空きのあるドライブを指定するか、空きを増やしてください。<br>足りないときは 1 段下のプロファイルに自動で下げます |
| ブラウザで話しかけても反応しない | ブラウザのマイク許可（アドレスバー左の鍵アイコン）を確認してください。<br>`http://127.0.0.1` はマイクが使える扱いです。<br>Windows の「プライバシー → マイク」でブラウザを許可する必要もあります |
| VRAM 不足（`CUDA out of memory`、LLM や TTS が起動しない） | ほかのアプリ（ゲーム・3D ソフト・ブラウザのハードウェアアクセラレーション）を閉じてください。<br>設定画面の LLM で、より軽いプロファイル（low）にするか、`--n-cpu-moe` を増やす（`profiles.py`）と VRAM を減らせます |
| 返答が遅い | 同梱の LLM は VRAM に載らない分を CPU で処理します。<br>low にするか、外部の推論サーバーを使ってください。<br>TTS は num_steps を小さくすると速くなります |
| ポートが使用中 | `setup.bat` と `start.bat` が空きポートに自動で付け替えます（既定: LLM 8081 / TTS 8088 / STT 8002 / アプリ 8100。8000 と 8080 は避けます） |
| 起動に失敗する | 管理画面（`http://127.0.0.1:8099/`、またはトレイの「ログ・状態」）の「ログ」を見てください。<br>ファイルは `logs\llm.log`・`tts.log`・`stt.log`・`app.log`・`supervisor.log` です。<br>報告するときは、管理画面の「不具合報告用にまとめてダウンロード」の zip を送ってください（キーは伏せ字です） |
| 管理画面が開かない・管理プロセスが動かない | `start.bat --console`（または `debug-start.bat`）で、従来どおり各サービスを**コンソールのウィンドウ**で起動できます（エラーがそのまま見えます）。<br>このときは管理画面・トレイはありません。<br>止めるときは `stop.bat` |
| トレイにアイコンが出ない | 本体は動いています。<br>Windows のタスクバーの「^」（隠れているインジケーター）の中を見てください。<br>管理画面の「終了」と `stop.bat` でも止められます |
| 返答に思考が混ざる | 設定画面の「思考を止める方法」を変えてください（モデルによって `reasoning_effort` か `chat_template_kwargs` のどちらかが効きます） |

## ツール（Web 検索・MCP・スキル・外部エージェント）

設定画面の「**ツール**」タブ（と `configure.bat`）で設定します。\
![ツール](docs/screenshots/settings_tools.png)

**ツールの定義は起動時に固定します**（会話の途中で変わると LLM のプロンプトキャッシュが効かなくなるため）。\
変更は「保存して再起動」で反映します。\
LLM タブの外部サーバーで「ツール呼び出しを使う」をオフにすると、ここのツールもすべて使えません。

### Web 検索（既定でオン）

- `web_search(query)`: DuckDuckGo（`ddgs`。API キー不要）の上位 5 件のタイトル・URL・要約を返します。\
  **検索語は外部（DuckDuckGo）に送られます。**
- `web_fetch(url)`: ページ本文をテキストで読みます。\
  http / https のみ。\
  127.0.0.1・10.x・192.168.x・リンクローカル・`file://` など、この PC や社内ネットワークの宛先は、リダイレクト先も含めて拒否します。\
  本文 1MB・15 秒に上限があります（名前解決の結果は接続の前後で確認しますが、DNS リバインディングへの対策は完全ではありません）。
- 調べた結果は話し言葉で短く要約し、URL は読み上げません。\
  最新の情報が必要なときだけ使います。
- 天気（Open-Meteo）と記憶も、このタブで個別にオフにできます。

### MCP

- `config/mcp.json`（Claude Desktop と同じ形式。そのまま貼り付けられます）。\
  例は `docs/mcp.example.json`。

  ```json
  {"mcpServers": {
    "demo": {"command": "python", "args": ["tools/mcp_test_server.py"], "allowed_tools": ["roll_dice", "current_time"]},
    "remote": {"url": "http://127.0.0.1:8765/mcp", "headers": {}, "enabled": false}
  }}
  ```

  `enabled`（既定 true）と `allowed_tools`（省略するとすべて許可）を足せます。\
  stdio は command + args（`python` はこのアプリの Python で代用、相対パスはインストール先が基準）、HTTP は streamable-http の URL です。
- 起動時に接続してツールを LLM に登録します。\
  **接続に失敗したサーバーは飛ばし**（警告ログ `logs\app.log` と、設定画面の「接続失敗」表示）、アプリは起動します。\
  サーバーの標準エラーは `logs\mcp_<名前>.log` です。
- 設定画面: サーバーの一覧（状態・ツール数）、JSON 編集、接続テスト（ツール一覧を出し、使うツールを個別に許可）、追加・削除。\
  env / headers の値は伏せ字で表示します。
- **注意**: MCP のツールはこの PC で動くプログラムで、ファイル操作やコマンド実行ができるものもあります。\
  **音声の聞き間違いがそのまま実行される**可能性があるので、信頼できるサーバーだけを追加し、使うツールを絞ってください。\
  ツールが多いと小さい LLM では呼び出しが不安定になります（目安: 標準のツールを含めた合計で low は 8 個、mid は 15 個、大きいモデルでも 30 個程度まで）。
- `npx` のサーバーには Node.js が必要です（自動では入れません。無ければ接続テストでそう表示します）。
- 試すには `tools/mcp_test_server.py`（サイコロ・時刻・足し算）を使えます。\
  HTTP は `app\.venv\Scripts\python.exe tools\mcp_test_server.py --http 8765`。

### スキル

- `skills/<name>/SKILL.md`（frontmatter に `name` と `description`、本文に手順。Claude の Agent Skills に似た形）。\
  同梱は「news-topics（今日のニュースの話題）」と「research（調べもの）」。
- システムプロンプトに入れるのは**一覧（name と description）だけ**で、本文は LLM が `use_skill(name)` で必要なときに読みます（プロンプトが短く、キャッシュも壊れません）。\
  起動時に読み込みます。\
  全キャラクターで共通です。
- 設定画面で、一覧・オン／オフ・新規作成と編集・削除ができます。

### 外部エージェント（既定オフ）

時間のかかる作業（ファイル操作・コード実行・長い調査）だけを、別の作業エージェントに頼みます。\
天気と簡単な検索はアバター自身のツールで済ませます。\
頼んだあとも会話は続き、完了すると結果をアバターが話して伝えます（`ask_agent` と進捗確認の `check_agent_tasks`。AIAvatarKit の `OpenClawTool` と `use_invoke_queue`。連携がオンのときだけ有効）。

- 種類は Hermes Agent / OpenClaw。\
  base_url（`/v1` まで。既定 `http://127.0.0.1:8642/v1`）・API キー（`config/settings.json` にだけ保存し、画面には再表示しません）・タイムアウト（既定 600 秒）。\
  「接続テスト」は `/health` と `/v1/models` を確かめます。
- **注意**: Hermes の API は、ターミナル実行を含む全ツールへのアクセスを与えます。\
  **127.0.0.1 の中だけで使い、API キーを必ず設定**してください。\
  Hermes 側の承認設定は緩めないでください。
- Hermes 本体のインストールはこのリポジトリの対象外です。\
  公式の導入ページ: <https://hermes-agent.nousresearch.com/docs/>

## Irodori-TTS の倫理的な制限（要約）

Irodori-TTS（MIT ライセンス）には、ライセンスに加えて次の利用上の制限があります。\
このプロジェクトでも守ってください。

1. **なりすまし禁止**: 声優・有名人・公人など、実在の個人の声を、本人の明確な同意なしにクローン・模倣しない。
2. **偽情報の禁止**: 他人を欺く、または偽情報を広める目的の合成音声（ディープフェイク）を作らない。
3. 参照音声なしで（キャプションだけで）作った声が、偶然に実在の人物に似ることがあるが、これは確率的な現象であり、特定の人物を再現する意図はない。
4. 開発者は誤用について責任を負わない。\
   生成物の利用が、お住まいの地域の法令に従うことは利用者の責任。

同梱の声（`voices/mio_a`〜`mio_d`）は、成人女性のキャプションと固定 seed の Voice Design で作ったもので、実在の人物の声ではありません（キャプション・seed・安定度は `voices/voices_meta.json`）。\
Irodori は生成した音声に透かし（SilentCipher）を入れます。

## 依存するソフトウェアのライセンス

| ソフトウェア | 用途 | ライセンス |
|---|---|---|
| このリポジトリ | | MIT |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | LLM の推論 | MIT |
| [AIAvatarKit](https://github.com/uezo/aiavatarkit) | 音声会話の基盤・ブラウザ UI | Apache-2.0 |
| [Irodori-TTS](https://github.com/Aratako/Irodori-TTS) / [Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) | 音声合成 | MIT |
| [Irodori-TTS-v4.1-Small（量子化）](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small-Quantized) | TTS のモデル | MIT（倫理的な制限あり） |
| [Semantic-DACVAE-Japanese-32dim](https://huggingface.co/Aratako/Semantic-DACVAE-Japanese-32dim) / [DACVAE](https://github.com/facebookresearch/dacvae) | TTS の codec | MIT / Apache-2.0 |
| [SilentCipher](https://github.com/sony/silentcipher) | 生成音声の透かし | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper)（Whisper small） | STT | MIT |
| [silero-vad](https://github.com/snakers4/silero-vad) | 音声区間検出 | MIT |
| [PyTorch](https://pytorch.org/) | TTS の実行 | BSD-3-Clause |
| [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)（GGUF: unsloth） | LLM（high） | Apache-2.0 |
| [Gemma 4](https://ai.google.dev/gemma/docs/gemma_4_license)（26B-A4B / E4B） | LLM（mid / low） | Gemma 4 のライセンス（Hugging Face 上の表示は Apache-2.0。利用前に原文を確認してください） |
| [Strata](https://github.com/Niko1221/Strata)（任意） | 高性能 PC 向けの LLM | MIT（モデルごとに別のライセンス。Strata 側の案内に従う） |
| [uv](https://github.com/astral-sh/uv) / [MinGit](https://github.com/git-for-windows/git) | 導入ツール | MIT・Apache-2.0 / GPL-2.0（必要なときだけ導入） |

ブラウザ UI の画像（キャラクター）は AIAvatarKit のサンプルを使わず、設定画面で選んだキャラクターの画像（無ければシルエット）を表示します。

## ファイル構成

```
setup.bat / setup.py   導入（スペック検出 → プロファイル選択 → 自動インストール）
start.bat / stop.bat   起動・停止（start.bat は koemado.vbs 経由でウィンドウなしに supervisor を起動。--console / debug-start.bat は従来のコンソール起動）
supervisor.py          常駐の管理プロセス（サービスの起動・見守り・自動再起動、ログ、管理画面、タスクトレイ）
console_ui/            管理画面（状態・ログ）の画面
koemado.vbs / .ico     ウィンドウなしの起動（ショートカットもこれを使う）、アイコン
configure.bat / .py    設定の対話 CLI（設定画面の予備）
ctl.py                 起動・停止・再起動・ダウンロード・ショートカット作成の共通処理（コンソール起動もここ）
profiles.py            LLM プロファイル表、モデルを選べるサイズの範囲、llama-server の引数
hf_hub.py              Hugging Face の API（検索・ファイル一覧・GGUF のメタデータ・トークンの確認）。トークンの送り先を huggingface.co に限る
settings_store.py      設定・キャラクターの読み書きと、成人チェック
tools_store.py         MCP 設定（config/mcp.json）とスキル（skills/）の読み書き
skills/                スキル（news-topics / research を同梱）
app/                   AIAvatarKit 本体（run.py）、設定画面（settings_api.py / settings_ui）、天気・記憶、Web 検索・MCP・スキル・外部エージェント
stt/                   faster-whisper の STT サーバー
voices/                同梱の参照音声（Voice Design で作った成人女性の声）
characters/            キャラクター（_common.md = 共通ルール、mio/ = サンプル）
tools/                 e2e_test.py（WebSocket テスト）、make_voices.py（Voice Design で声を作る）、mcp_test_server.py（MCP の確認用）
```

導入後にできるもの（Git には入れません）: `strata/` `strata-data/`（Strata を選んだとき）`config.json` `config/` `llama.cpp/` `models/` `tts/` `hf-home/` `torch-home/` `app/.venv` `stt/.venv` `app/html/` `app/data/` `logs/` など。
