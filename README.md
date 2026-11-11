# bookscan-epub

`~/src/bookscan/pdf/` の画像PDFをもとに、サニタイズ済みPDFへ透明テキスト層を重ねる実験用リポジトリ。原本の保管とダウンロードは別プロジェクト `~/src/bookscan/` が担当する。リポジトリ名は旧EPUB方針の名残。

## 現在の段階

『虎よ、虎よ！』『インフィニティ・リミテッド（上）』『夏への扉』の検索可能PDFを作成した。続けて傾き補正込みの手順で『時間泥棒』『暗黒星雲突破！』『天の光はすべて星』『インフィニティ・リミテッド（下）』を作り、ローカルLLMで作った濁点・半濁点の辞書で補正した版も作った。[成果物の目次](artifacts.html)から完成PDF、辞書、レビューを開ける。目次の原稿は `artifacts.md` で、`bin/build-docs-html.sh` がこのREADMEや作業記録と一緒にHTMLにする（mdへのリンクはhtmlへのリンクになる）。原本・抽出画像・OCR結果は `.gitignore` で管理対象から外す。

### 現在の1冊分の手順

原本は `input/bookscan-downloads/`、Pythonは `.venv-ndl`、NDLOCR-Liteは `vendor/ndlocr-lite/src`、YomiTokuは `vendor/.venv-yomitoku/bin/yomitoku`。作業は `work/searchable/作業名/`、レビュー画面は `review/作業名-voicing/` と `review/作業名-judge/`、完成品は `output/searchable/書名 - 著者.pdf`。`MAX_PAGES=8` を付けると、先頭8ページだけの試運転（作業ディレクトリは別名）になる。

段の順は、閲覧用PDF（背景補正と傾き補正）→ NDLOCR-Lite → 余白つき再認識 → YomiToku → パ／バ → 判断モデル → 文字層 → 書誌設定。
- **パ／バ：** 語を切り出して読み直す自動判定で、安全な組だけ確定する。残りは人がレビュー画面で確定する。
- **判断モデル：** NDLとYomiTokuの残りの食い違いを、ローカルLLMが判断する。
- **文字層：** パ／バの辞書、LLMと人の差分、`char-map.tsv` を当てる。

OCR、余白つき再認識、YomiTokuは、済んでいれば飛ばす。パ／バ、判断モデル、文字層は、入力の中身が変わったときだけ作り直す（ファイルの日付は見ない）。変わる完成品は消さずに、`output/searchable/old/` へ日付を付けて移す。

YomiTokuかLLM（llama-server）が見つからないと止まる。使わないなら `SKIP_YOMITOKU=1` や `SKIP_JUDGE=1` を付ける。1冊の中で、段の有無とデバイスは最初の実行のまま変えられない（`stages`・`device` に記録）。これらの段より前に作った作業ディレクトリは、`UPGRADE=1` を付けない限り何も変えない。

```sh
SOURCE=0101-20150604105123153_check.pdf   # data/bookscan-catalog.tsv の source
NDLOCR_DEVICE=cuda bin/build-searchable-book.sh "$SOURCE" jikan-dorobou
# 最後に、2つのレビュー画面と、まだ人が見ていない判断の数が表示される。
# 画面で選んだら「TSVをコピー」して保存し、同じコマンドをもう一度実行する（パ／バを先に）。
wl-paste > work/searchable/jikan-dorobou/voicing-human.tsv
wl-paste > work/searchable/jikan-dorobou/judge-human.tsv
# 本全体で原本と違う字を使うなら、対応表を置いてから再実行する（例: 掴を摑に）
printf 'from\tto\n掴\t摑\n' > work/searchable/jikan-dorobou/char-map.tsv

# 傾き補正したページの文字層の位置を確認（列の縦ずれの傾きが0付近ならよい）
.venv-ndl/bin/python bin/check-deskew-text.py \
  work/searchable/jikan-dorobou/angles.tsv work/searchable/jikan-dorobou/text-unfinalized.pdf
```

パ／バのレビューを先に済ませる。パ／バの辞書が変わると、判断モデルの差分の前後4文字も変わる。どの差分にも当たらない人の判定が出ると、判断モデルの段は止まる。

3番目の引数にLLMで作った辞書（`bin/auto-variant-glossary.py`）を渡すと、パ／バと判断モデルの段を飛ばして、その辞書で「辞書補正」版を作る（以前の手順）。

OCRは1ページ約1.2秒、余白つき再認識は約3秒（CPU）。YomiTokuは約0.7秒（GPU）。語の切り出しの読み直しは1冊1分前後。判断モデルは、初回に1冊で200〜300か所を聞いて約10分かかる。聞いた結果は `work/searchable/作業名/judge/history.tsv` に残り、同じ差分は聞き直さない。考え方と検証結果は [docs/progress.md](docs/progress.md) の「傾き補正込みで4冊を作成」と「1冊を通す手順への組み込み」。

GPUを使う場合は、`.venv-ndl` の `onnxruntime` を `onnxruntime-gpu[cuda,cudnn]` に入れ替え、`NDLOCR_DEVICE=cuda` を付ける。OCRは約0.45秒、余白つき再認識は約0.75秒になる（RTX 3060）。CPUとGPUは約2%の行で読みが変わるが、精度は同等。そのため1冊は同じデバイスで通す。作業ディレクトリの `device` に最初のデバイスを記録し、違うデバイスでの再開は止まる。GPUが使えずCPUに切り替わった場合も止まる。入れ替え後も `NDLOCR_DEVICE=cpu`（既定）なら、結果は `onnxruntime` と同じ。検証は [docs/progress.md](docs/progress.md) の「GPU（CUDA）でのOCRと余白つき再認識」。

```sh
.venv-ndl/bin/pip uninstall -y onnxruntime
.venv-ndl/bin/pip install 'onnxruntime-gpu[cuda,cudnn]==1.31.1'
NDLOCR_DEVICE=cuda bin/build-searchable-book.sh "$SOURCE" jikan-dorobou

# OCRの比較用標本（3冊141ページ）と、実行結果の比較
.venv-ndl/bin/python bin/ocr-sample-pages.py work/ocr-engine-check
.venv-ndl/bin/python bin/compare-ocr-runs.py work/ocr-engine-check cpu saved cuda
.venv-ndl/bin/python bin/score-margin-runs.py work/ocr-engine-check ocrcpu-reccpu ocrcuda-reccuda
```

`compare-ocr-runs.py` は `work/ocr-engine-check/書籍/実行名/` にある `ocr.py --output` の結果を読む（`saved` は全冊OCRの保存分）。`score-margin-runs.py` は `書籍/margin-実行名.tsv`（`margin-recognize.py` の出力）を読む。

濁点・半濁点（バ／パ）の取り違えは、`bin/build-voicing-review.py` が作るレビュー画面で人が確定して辞書にする。候補は、本の中で両方の表記が出る組と、YomiTokuが別の表記に読んだ組。組ごとに、ページの縮小画像・行・3倍拡大を並べる。選んだ結果は画面の下にTSVでまとまるので、「TSVをコピー」で保存して辞書として渡す。

```sh
SLUG=jikan-dorobou; STEM=${SOURCE%.pdf}; WORK=work/searchable/$SLUG
../bookscan/vendor/.venv-yomitoku/bin/yomitoku input/bookscan-downloads/$SOURCE \
  -f json -o work/voicing/$SLUG/yomitoku -d cuda --dpi 300 --ignore_ruby
.venv-ndl/bin/python bin/build-voicing-review.py input/bookscan-downloads/$SOURCE \
  $WORK/ocr review/$SLUG-voicing --ocr-stem "$STEM" --recognized $WORK/margin.tsv \
  --yomitoku-dir work/voicing/$SLUG/yomitoku --title "$SLUG 濁点・半濁点レビュー"
firefox-developer-edition review/$SLUG-voicing/index.html
# 画面でコピーしたTSVを $WORK/voicing-glossary.tsv に保存してから
bin/build-searchable-book.sh "$SOURCE" $SLUG $WORK/voicing-glossary.tsv
```

`--yomitoku-dir` は省略できる（本の中で両方の表記が出る組だけになる）。確定した辞書を正解として自動判定を採点するには、`bin/check-voicing-judges.py 原本PDF OCRディレクトリ 辞書TSV 出力TSV --ocr-stem STEM --recognized margin.tsv --yomitoku-dir DIR --device cuda` を使う。語を原本から切り出して読み直す判定と、`--llm-sample N` を付けたときのLLMの判定（llama-server）を出現箇所ごとに比べる。`build-searchable-book.sh` が受け取る辞書は1つなので、LLMで作った `variant-glossary.tsv` とは別の版になる。

規則と画像で直せなかった、NDLとYomiTokuの読みの食い違い（漢字の取り違えなど）は、`bin/judge-ocr-disagreements.py` でローカルLLMに判断させる。2つのOCRの行を座標で対応させて、差分ごとに分ける。パ／バ、括弧、`"`（あとで〝〟に組み直す）、ダッシュ、濁点の付け外し、小書きの仮名、片方にしかない文字は聞かない。残りを、行の2通りの表記で順序を入れ替えて2回聞き、2回ともYomiTokuを選んだ差分だけを `corrections.tsv` に書き出す。聞いた結果は `history.tsv` に追記され、同じ差分は聞き直さない。レビュー画面では、反映した差分を先頭に並べる。画面で選んだ結果をTSVで保存して `--human` に渡すと、人の判定が常に優先される。差分は `rebuild-text-layer.py --corrections` が、ほかの補正のあと、引用符を組み直す前に当てる。前後4文字が一致しないと、適用せずに止まる。NDLの字が新字体で、YomiTokuの字がその旧字体（掴／摑、顛／顚など）の差分は、聞かずにYomiTokuを採る。規則で聞かなかった差分も、人の判定があればそれを使う。線や記号（一・―・ー・「」など）の差分は、レビュー画面で6倍に拡大して印を付ける。本全体で原本と違う字を使い分けている場合（この本はすべて摑なのに、OCRは掴と読む箇所が残るなど）は、`rebuild-text-layer.py --char-map 対応表TSV`（列は `from`・`to`）で、差分を当てたあとに1文字ずつ置き換える。

```sh
.venv-ndl/bin/python bin/judge-ocr-disagreements.py input/bookscan-downloads/$SOURCE $WORK/ocr \
  work/voicing/$SLUG/yomitoku work/judge/$SLUG review/$SLUG-judge --ocr-stem "$STEM" \
  --recognized $WORK/margin.tsv --glossary $WORK/voicing-glossary.tsv \
  --human work/eval/$SLUG-judge-human.tsv --title "$SLUG LLM判断のレビュー"
# rebuild-text-layer.py に --corrections work/judge/$SLUG/corrections.tsv を足して文字層を作る
```

ほかのOCRとは `bin/score-ocr-engines.py` で比べる。エンジンは `ndl:実行名`・`margin:実行名`・`yomitoku:実行名` で指定する。行の区切りや記号の表記に左右されない採点で、`--diff A B` を付けると、無作為ページでAとBが食い違った行を出す。YomiTokuは別の環境で動かす。

```sh
../bookscan/vendor/.venv-yomitoku/bin/yomitoku work/ocr-engine-check/tiger/subset.pdf \
  -f json -o work/ocr-engine-check/tiger/yomitoku -d cuda --dpi 300 --ignore_ruby
.venv-ndl/bin/python bin/score-ocr-engines.py work/ocr-engine-check \
  ndl:cpu margin:ocrcpu-reccpu yomitoku:yomitoku --diff margin:ocrcpu-reccpu yomitoku:yomitoku
```

以下は以前のOCR比較で使ったコマンド。このチェックアウトには当時の `.venv-ndl/` と作業データがなく、原本は隣の `bookscan/pdf/` にある。原本と生成物はGitに含めない。

```sh
bin/extract-page.sh ../bookscan/pdf/0101-20150604103726242_check.pdf 12

mkdir -p work/ndl/page-012 work/paddle/page-012
.venv-ndl/bin/ndlocr-lite \
  --sourceimg work/sample/page-012-000.jpg \
  --output work/ndl/page-012 --viz True

PADDLE_PDX_CACHE_HOME="$PWD/.paddlex" .venv-paddle/bin/paddleocr ocr \
  -i work/sample/page-012-000.jpg --device cpu \
  --lang japan --ocr_version PP-OCRv6 \
  --use_doc_orientation_classify False \
  --use_doc_unwarping False \
  --use_textline_orientation False \
  --save_path work/paddle/page-012

ruby bin/compare-ocr.rb \
  work/ndl/page-012/page-012-000.json \
  work/paddle/page-012/page-012-000_res.json

# 差がある文字位置も表示
ruby bin/compare-ocr.rb --details \
  work/ndl/page-012/page-012-000.json \
  work/paddle/page-012/page-012-000_res.json

# 原本画像と2つのOCRを1画面でレビュー（Firefoxで直接開ける）
.venv-ndl/bin/python bin/recognize-ruby.py \
  work/sample/page-012-000.jpg \
  work/ndl/page-012/page-012-000.xml \
  work/ruby/page-012.json
ruby bin/build-review.rb \
  work/sample/page-012-000.jpg \
  work/ndl/page-012/page-012-000.json \
  work/paddle/page-012/page-012-000_res.json \
  review/page-012.html \
  work/ruby/page-012.json
firefox-developer-edition review/page-012.html
```

PaddleOCR 3.7.0 の PP-OCRv6 では日本語の `--lang` は `ja` ではなく `japan`。各OCRのJSONは別々に保存し、比較時に座標で縦列を対応付ける。比較値は食い違いの量であり、正解率ではない。現状、PaddleOCRは検証用の参考表示にとどめ、本文の自動採用・多数決には使わない。

NDLOCR-LiteのJSONと同じ場所にXMLがあれば、比較スクリプトは本文に対応しないPaddleOCR領域をルビ・ノンブルとして分類する。ページ12ではルビ「きぜん」とノンブル「10」を分類できた。

レビューHTMLは画像を埋め込んだ単独ファイル。列ごとに差を黄色で示し、列を選ぶと原本の位置を赤枠で表示する。ルビはNDLOCR-Liteの検出枠を切り出して付属の3つの文字認識モデルに読ませ、PaddleOCRと並べる。ノンブルは本文照合前に除外する。生成物は `review/` に置き、Gitには含めない。

追加評価の [PDF 4ページ](review/page-004.html)、[PDF 19ページ](review/page-019.html)、[PDF 20ページ](review/page-020.html) もローカルに生成済み。ルビの原本確認にはページを開いてルビの行を選ぶ。未分類のPaddleOCR候補には認識スコアを表示し、低スコアは「未分類・低信頼」と区別する。

原本画像で確認した3か所のルビは `eval/ruby-gold.tsv` に記録した。`ruby bin/score-ruby.rb` で保存済みOCR結果との完全一致を再計算できる。判読がまだ曖昧な箇所は採点対象に入れない。

PDF 4ページの登場人物一覧から作った辞書候補は `data/cast-seed.tsv`。`confirmed` は画像で表記を確認済み、`needs_review` はOCR間の不一致などが残っている。未確認候補は本文の自動修正に使わない。

登場人物一覧のように点線リーダーがあるページは、比較時に `--ignore-leaders` を付けると繰り返しの `……` / `...` だけ無視できる。名前中の `・` と `ー` は差分として残す。例えば:

```sh
ruby bin/compare-ocr.rb --details --ignore-leaders \
  work/ndl/page-004/page-004-000.json \
  work/paddle/page-004/page-004-000_res.json

ruby bin/build-review.rb --ignore-leaders \
  work/sample/page-004-000.jpg \
  work/ndl/page-004/page-004-000.json \
  work/paddle/page-004/page-004-000_res.json \
  review/page-004.html
```

設計と進捗は [docs/progress.md](docs/progress.md) に記録する。[data/epub-candidates.tsv](data/epub-candidates.tsv) は旧EPUB方針での一次選別結果であり、検索可能PDFの対象を絞る根拠には使わない。

## 成果物の保管（Google Drive）

`output/` と `work/` はGitに入らない。そのため、完成品はGoogle Driveの `bookscan-epub/` に、このリポジトリと同じフォルダ構成で置く。Driveには `rclone` のリモート `gdrive:` で接続する。リモートは自分で作ったOAuthクライアントIDを使い、scope は `drive.file`（rcloneが置いたファイルとフォルダにだけ触れる）。ブラウザから入れたファイルは、rcloneからは見えない。OAuthアプリは本番に公開してあるので、許可は期限切れにならない。ホームページとプライバシーポリシーは https://kusanaginoturugi.github.io/rclone/ に置いてある。別のマシンでは `~/.config/rclone/rclone.conf` の `[gdrive]` をコピーするか、同じクライアントIDで `rclone config` する。

```sh
# このマシンからDriveへ（既存のファイルは消さない）
rclone copy output gdrive:bookscan-epub/output
rclone copy work/searchable gdrive:bookscan-epub/work/searchable --include '*.tsv'
rclone copy work/eval gdrive:bookscan-epub/work/eval --include '*.tsv'   # 人が確定した辞書・正解
rclone copy work/judge gdrive:bookscan-epub/work/judge   # LLM判断の履歴と適用する差分
# 別のマシンでDriveから戻す（artifacts.html のリンクがそのまま開ける）
rclone copy gdrive:bookscan-epub/output output
rclone copy gdrive:bookscan-epub/work/searchable work/searchable
rclone copy gdrive:bookscan-epub/work/eval work/eval
rclone copy gdrive:bookscan-epub/work/judge work/judge
```

`rclone sync` は送り先にしかないファイルを消すので使わない。中身は買った本のスキャンなので、共有リンク（`rclone link` やブラウザの「リンクを知っている全員」）は作らない。

## BOOKSCAN PDF の閲覧用コピー

原本は別プロジェクトの `../bookscan/pdf/`、このプロジェクトで作る閲覧用コピーは `output/readable-pdf/`。83冊の書誌候補は `data/bookscan-catalog.tsv` に記録し、元のランダム名との対応を残す。書名と著者から `書名 - 著者.pdf` を作る。書誌情報は主に表紙OCRから作った候補なので、必要に応じて原画像で校正する。

```sh
/usr/bin/python bin/prepare-books.py
```

`bin/prepare-books.py` は `pikepdf`・`scipy`・`numpy`・`Pillow` を使い、シバンは `/usr/bin/python`。現在の環境では `/usr/bin/python` にも `../bookscan/vendor/.venv-ndlocr` にも `pikepdf` がなく、`/usr/bin/python` には `scipy` もないため、このままでは動かない。`output/readable-pdf/` も現在のチェックアウトにはない。検索可能PDFの試験では、代わりに `bin/prepare-trial-pdf.py`（`pypdf` と `cv2` を使う1冊ずつの版）で `output/trial/*-sanitized.pdf` を作っている。

このスクリプトは画像PDFの本文ページを判別し、紙色が強いページだけ背景を白く補正する。縦組みの列から傾きを確信できたページのみ回転する。ページごとの表示サイズが揺れないよう、余白は切らずに原本のページ枠を保持する。表紙・カラー図版の画像は再圧縮しない。PDFの基本情報には書名と著者を、XMPには翻訳者・文庫名・元ファイル名を記録する。完了冊数と処理数は `output/readable-pdf/conversion.tsv` で確認できる。

処理は途中で再実行できる。既に全ページがある出力PDFは飛ばし、ページ数が不足する試験出力は作り直す。原本はそのまま残す。
書誌を修正した本だけ作り直す場合は `--only 元ファイル名 --force` を使う。

## 検索可能PDFへの方針変更

このプロジェクトで生成する閲覧用コピーの各ページ画像とページ枠を保持し、NDLOCR-Liteの本文認識結果を透明なテキスト層として重ねる。縦書きは原本の列順（右から左）と列内の行順（上から下）で文字抽出できるようにし、座標とテキストの対応も保持する。傾き補正するページでは補正前の画像もOCRし、補正後の画像へ文字座標を変換する。別プロジェクトの原本PDF、閲覧用PDF、OCR結果は別々に保存する。現在の試験では、閲覧用コピーとして `bin/prepare-trial-pdf.py` で作った `output/trial/*-sanitized.pdf` を使う。傾き補正前のOCR座標を補正後の画像へ移す変換は `bin/deskew-study.py` で6ページだけ試し、1冊分の処理には組み込んでいない。『インフィニティ・リミテッド（上）』は `--no-deskew` で傾き補正せずに作った。

### 3冊目：『夏への扉』（余白つき再認識を最初から使う手順）

完成品は `output/trial/夏への扉 - ロバート・A・ハインライン.pdf`（Git管理外）。356ページ。保存済みOCRのない本に、上巻で決めた規則を最初から当てた。原本をOCRして行枠を得たあと、`bin/margin-recognize.py` で認識モデルだけを余白つきで再実行する。高さ400px以上の縦書き行は余白30pxの読み、それより低い行（短い会話行・ノンブル・奥付など）は保存済みOCRの読みを使い、`--fix-dashes` を当てる。さらに、画像で確認した15語の辞書 `work/eval/summer-door-glossary.tsv` と `--fix-variants` で、濁点と半濁点の取り違え（ビート→ピート、ポストン→ボストンなど）を直す。個別補正はまだない。

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
STEM=0101-20150604095752068_check
$PY bin/prepare-trial-pdf.py "${STEM}.pdf" output/trial/summer-door-sanitized.pdf --no-deskew
$PY ../bookscan/vendor/ndlocr-lite/src/ocr.py \
  --sourcepdf ../bookscan/pdf/"${STEM}.pdf" \
  --output work/trial/summer-door-ocr \
  --pdf-output output/trial/summer-door-original-ocr.pdf \
  --pdf-render-dpi 300
$PY bin/margin-recognize.py work/margin/summer-door.tsv \
  --pdf ../bookscan/pdf/"${STEM}.pdf" \
  --ocr-dir work/trial/summer-door-ocr --ocr-stem "$STEM"
$PY bin/rebuild-text-layer.py \
  output/trial/summer-door-sanitized.pdf work/trial/summer-door-ocr \
  output/trial/summer-door-text-unfinalized-v2.pdf --ocr-stem "$STEM" \
  --recognized work/margin/summer-door.tsv --column margin30 --min-height 400 --fix-dashes \
  --glossary work/eval/summer-door-glossary.tsv --fix-variants
$PY bin/finalize-trial-pdf.py \
  output/trial/summer-door-sanitized.pdf \
  output/trial/summer-door-text-unfinalized-v2.pdf \
  'output/trial/夏への扉 - ロバート・A・ハインライン.pdf' \
  --catalog-source "${STEM}.pdf"
```

OCRは約7分、余白つき再認識は約17分（CPUのみ）。`--min-height` より低い行は `--recognized` の列を使わず保存済みOCRの読みを残す。`--fix-variants` は辞書の確認済みの語について、バ行とパ行の1文字だけが違う読みを辞書の表記に置き換え、置き換えた件数を表示する。長いカタカナ語の一部（ペルシャのペルなど）や、辞書に載っている別の語は置き換えない。行をまたいで分かれた語は対象外。辞書候補は `bin/extract-glossary-candidates.py` で抜き出し、`work/eval/summer-door-glossary-candidates.tsv` に置いた。

### 『夏への扉』の読書用注釈試作

`data/summer-door-reading-notes.json` の2項目を、完成PDFのページ21・23・24にPDF標準のメモ注釈として埋め込んだ。注釈はページ上部の余白に置き、本文画像と文字層は変更しない。完成品は残し、別ファイル `output/trial/夏への扉 - ロバート・A・ハインライン 注釈試作 Firefox版 薄色右開き.pdf`（Git管理外）を作る。Firefoxの黄色い注釈アイコンを隠し、淡い灰色の吹き出しをページに描いた。吹き出しはマウスオーバーで説明を表示し、クリックで固定する。隣の「↗」は出典ページへのリンクで、Firefox内蔵PDFビューアが新しいタブで開く指定を付けた。本文の偶数ページ176枚の左端に残ったスキャン線を、白い細帯で覆った。Readest向けにPDFの `/ViewerPreferences /Direction /R2L` を設定し、見開きを右開きとして自動判定できるようにした。追加候補は [`data/summer-door-note-candidates.md`](data/summer-door-note-candidates.md) に、1956年以前の現実の情報だけを使って選定した。

```sh
../bookscan/vendor/.venv-ndlocr/bin/python bin/embed-reading-notes.py \
  'output/trial/夏への扉 - ロバート・A・ハインライン.pdf' \
  data/summer-door-reading-notes.json work/trial/summer-door-ocr \
  0101-20150604095752068_check \
  '/tmp/bookscan-summer-door-pale-notes.pdf'
../bookscan/vendor/.venv-ndlocr/bin/python bin/mask-scan-edge.py \
  '/tmp/bookscan-summer-door-pale-notes.pdf' \
  '/tmp/bookscan-summer-door-pale-edge.pdf' \
  --even-pages 4-354 --side left --pixels 8
../bookscan/vendor/.venv-ndlocr/bin/python bin/set-pdf-direction.py \
  '/tmp/bookscan-summer-door-pale-edge.pdf' \
  'output/trial/夏への扉 - ロバート・A・ハインライン 注釈試作 Firefox版 薄色右開き.pdf' \
  --direction rtl
```

### 2冊目の試験：『インフィニティ・リミテッド（上）』

完成品は `output/trial/インフィニティ・リミテッド（上） - ジェイムズ・P・ホーガン.pdf`（Git管理外）。388ページの原本を補正前にOCRし、画像を保持したPDFへ透明テキスト層を重ねた。画像で表記を確認した13語は `work/eval/infinity-upper-glossary.tsv`、個別のOCR補正7行は `work/eval/infinity-upper-overrides.tsv` に記録した。全文から抽出した未確認の頻出語候補は `work/eval/infinity-upper-glossary-candidates.tsv` に分け、自動補正には使わない。

画像と文字層のレビューは `review/infinity-upper-eval/index.html`、辞書候補上位30語の画像は `review/infinity-upper-glossary/index.html`、ローカルLLMで選んだ20行は `review/infinity-upper-llm/index.html` にある。これらもGit管理外。方法と結果は [docs/progress.md](docs/progress.md) に記録した。

傾き補正候補6ページの前後OCR比較と、補正前OCRの座標を回転して重ねた試験PDFは [比較レビュー](work/deskew-study/index.html) にまとめた。`bin/deskew-study.py --score` で保存済みの正解行を再採点できる。完成品388ページには補正を反映していない。

`bin/ocr-sensitivity.py` は同じ6ページで保存済みの行枠を使い、NDLOCR-Liteの認識モデルだけを再実行する。行枠の幅、画素の揺らし、余白の追加（`--pad-sweep`）で認識結果がどう変わるかを `work/ocr-sensitivity/` に出す。

`bin/margin-recognize.py` は1冊分の保存済みOCRの行枠を使い、縦書きの行画像に余白を足して認識モデルだけを再実行する。`--score` で正解TSVと照合する。

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
$PY bin/margin-recognize.py work/margin/infinity-upper.tsv \
  --pdf ../bookscan/pdf/0101-20150604103726242_check.pdf \
  --ocr-dir work/trial/infinity-upper-ocr --ocr-stem 0101-20150604103726242_check
$PY bin/margin-recognize.py work/margin/infinity-upper.tsv \
  --score work/margin/gold-random.tsv work/margin/gold-existing.tsv
```

余白30pxの結果で文字層を作り直した版は `output/trial/インフィニティ・リミテッド（上） - ジェイムズ・P・ホーガン v2.pdf`（Git管理外）。従来の完成品と並べて置き、置き換えるかは未決定。v2は `--min-height` を付けずに作ったため、短い行の閉じかぎ括弧の脱落などを含む（[docs/progress.md](docs/progress.md)）。`--fix-dashes` は縦書き行で、前後どちらも英数字でない `-` を `―` にする。`--fix-brackets` は縦書き行で `<>` `＜＞` を〈〉にし、`"` をページ内の読み順で〝〟の開き・閉じが交互になるよう戻す。数が合わないページは `"` のまま残し、ページ番号を表示する。`bin/build-searchable-book.sh` は常にこのオプションを付けるが、作成済みの本の文字層には入っていない。

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
STEM=0101-20150604103726242_check
$PY bin/rebuild-text-layer.py \
  output/trial/infinity-upper-sanitized.pdf work/trial/infinity-upper-ocr \
  output/trial/infinity-upper-text-unfinalized-v2.pdf --ocr-stem "$STEM" \
  --glossary work/eval/infinity-upper-glossary.tsv \
  --overrides work/eval/infinity-upper-overrides.tsv \
  --recognized work/margin/infinity-upper.tsv --column margin30 --fix-dashes
$PY bin/finalize-trial-pdf.py \
  output/trial/infinity-upper-sanitized.pdf \
  output/trial/infinity-upper-text-unfinalized-v2.pdf \
  'output/trial/インフィニティ・リミテッド（上） - ジェイムズ・P・ホーガン v2.pdf' \
  --catalog-source "${STEM}.pdf"
```

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
STEM=0101-20150604103726242_check
$PY bin/prepare-trial-pdf.py "${STEM}.pdf" output/trial/infinity-upper-sanitized.pdf --no-deskew
$PY ../bookscan/vendor/ndlocr-lite/src/ocr.py \
  --sourcepdf ../bookscan/pdf/"${STEM}.pdf" \
  --output work/trial/infinity-upper-ocr \
  --pdf-output output/trial/infinity-upper-original-ocr.pdf \
  --pdf-render-dpi 300
$PY bin/rebuild-text-layer.py \
  output/trial/infinity-upper-sanitized.pdf work/trial/infinity-upper-ocr \
  output/trial/infinity-upper-text-unfinalized.pdf --ocr-stem "$STEM" \
  --glossary work/eval/infinity-upper-glossary.tsv \
  --overrides work/eval/infinity-upper-overrides.tsv
$PY bin/finalize-trial-pdf.py \
  output/trial/infinity-upper-sanitized.pdf \
  output/trial/infinity-upper-text-unfinalized.pdf \
  'output/trial/インフィニティ・リミテッド（上） - ジェイムズ・P・ホーガン.pdf' \
  --catalog-source "${STEM}.pdf"
```

ルビを本文へ組み込むEPUB特有の処理は不要になる。ルビ画像は残るが、読み上げ時の発音やルビ自体の検索は本文OCRだけでは保証できない。必要になった場合に限り、ルビを別途OCRして扱いを決める。読み上げはPDF閲覧アプリの機能と抽出順に依存するため、実際に使うアプリで確認する。実装と検証手順は [docs/progress.md](docs/progress.md) の最新方針を参照。

### 1冊目の試験：『虎よ、虎よ！』

旧選別で文字配置が重要とした本を364ページ処理した。空白修正版は `output/trial/虎よ、虎よ！ - アルフレッド・ベスター 空白修正.pdf`（Git管理外）。`../bookscan/vendor/.venv-ndlocr/bin/python` とNDLOCR-LiteのPDF文字層機能を使用する。再現手順:

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
SOURCE=0101-20150604101257195_check.pdf
mkdir -p output/trial work/trial/tiger-full
$PY bin/prepare-trial-pdf.py "$SOURCE" output/trial/tiger-sanitized.pdf
$PY ../bookscan/vendor/ndlocr-lite/src/ocr.py \
  --sourcepdf output/trial/tiger-sanitized.pdf \
  --output work/trial/tiger-full \
  --pdf-output output/trial/tiger-searchable-unfinalized.pdf \
  --pdf-render-dpi 300
$PY bin/finalize-trial-pdf.py \
  output/trial/tiger-sanitized.pdf \
  output/trial/tiger-searchable-unfinalized.pdf \
  'output/trial/虎よ、虎よ！ - アルフレッド・ベスター.pdf'
$PY bin/rebuild-text-layer.py \
  output/trial/tiger-sanitized.pdf work/trial/tiger-full \
  output/trial/tiger-text-normalized.pdf \
  --overrides work/eval/tiger-overrides.tsv \
  --glossary work/eval/tiger-glossary.tsv
$PY bin/finalize-trial-pdf.py \
  output/trial/tiger-sanitized.pdf output/trial/tiger-text-normalized.pdf \
  'output/trial/虎よ、虎よ！ - アルフレッド・ベスター 空白修正.pdf'
```

`prepare-trial-pdf.py`・`rebuild-text-layer.py`・`finalize-trial-pdf.py`・`margin-recognize.py` と、`deskew-study.py` の準備段階は、出力が既にあると止まる。`ocr-sensitivity.py` は `variants.tsv`（`--pad-sweep` では `pad-sweep.tsv`）があると止まる。`evaluate-trial-pdf.py`・`rank-ocr-lines.py`・`build-glossary-review.py`・`extract-glossary-candidates.py` と、`deskew-study.py --score` の `report.json`・`index.html` は既存の出力を上書きする。この本で傾き補正したページは0なので、上の手順では閲覧用PDFを直接OCRした。全364ページのOCR結果は `work/trial/tiger-full/` に残る。検索・画像照合の結果と特殊な組版ページの制約は [docs/progress.md](docs/progress.md) に記録した。読み上げアプリでの実聴は未確認。

### OCR文字層の評価

`bin/evaluate-trial-pdf.py` は通常ページ10件を固定シードで選び、例外ページと抽出が空のページも画像でレビューできるHTMLを作る。画像と照合した正解行を `work/eval/tiger-gold.tsv` に、検索したい語を `work/eval/tiger-queries.tsv` に保存する。これらの本文データはGit管理外。

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
$PY bin/evaluate-trial-pdf.py \
  'output/trial/虎よ、虎よ！ - アルフレッド・ベスター 空白修正.pdf' \
  work/trial/tiger-full review/tiger-eval \
  --gold work/eval/tiger-gold.tsv \
  --queries work/eval/tiger-queries.tsv --normalized-text-layer \
  --overrides work/eval/tiger-overrides.tsv \
  --glossary work/eval/tiger-glossary.tsv
```

結果は `review/tiger-eval/index.html` と `review/tiger-eval/report.json`。通常ページ10行・425文字では編集差0、例外ページ5行・54文字では編集差3。画像で確認した検索語12件のうち5件はPDF文字層に見つからず、いずれも特殊組版ページだった。この小標本を本全体の正解率とみなさない。詳しくは [進捗と設計メモ](docs/progress.md)。

`--ocr-stem` の既定値 `tiger-sanitized` と `--special-pages` の既定値 `155,192,257,311,316,321` は『虎よ、虎よ！』用。上の例はOCRのファイル名が `tiger-sanitized_*.json` なので既定値で動く。『インフィニティ・リミテッド（上）』の `review/infinity-upper-eval/` は次の指定で作った。完成品の文字層は4ページ・行10を直す前の補正で作ったため、直した補正ファイルで再実行すると4ページが不一致として報告される。`--recognized` で作ったv2の文字層は保存済みOCRのJSONと一致しないので、このスクリプトでは評価できない。

```sh
PY=../bookscan/vendor/.venv-ndlocr/bin/python
STEM=0101-20150604103726242_check
$PY bin/evaluate-trial-pdf.py \
  'output/trial/インフィニティ・リミテッド（上） - ジェイムズ・P・ホーガン.pdf' \
  work/trial/infinity-upper-ocr review/infinity-upper-eval --ocr-stem "$STEM" \
  --queries work/eval/infinity-upper-queries.tsv --normalized-text-layer \
  --overrides work/eval/infinity-upper-overrides.tsv \
  --glossary work/eval/infinity-upper-glossary.tsv \
  --special-pages 4,12,19,20,49,100,113,285,311,367,376
```

`bin/extract-glossary-candidates.py` はPDF全文から繰り返し現れるカタカナ語を集める。今回の候補100件は `work/eval/tiger-glossary-candidates.tsv`（Git管理外）に保存した。原本画像で確認した語だけを `work/eval/tiger-glossary.tsv` の `verified=yes` として使う。語中の空白だけをつなぎ、話者名とセリフの区切りなどは残す。

ルビ近傍の空白などを優先してローカルLLMに文章の違和感を採点させる場合は、OpenAI互換のローカルサーバーを起動してから `bin/rank-ocr-lines.py` を使う。`--url` でサーバーを、`--glossary` で確認済み辞書を指定できる。今回の20行の結果は `review/tiger-llm/index.html` と `ranked.tsv`（Git管理外）。レビューには元OCRと修正版PDFの文字層を両方表示する。高スコアにも詩や意図的な反復が混じるため、原本画像で確認してから修正する。画像で確認した個別補正は `work/eval/tiger-overrides.tsv`（Git管理外）に記録する。
