# bookscan-epub

画像PDFから、文字サイズを変更できるEPUBを作るための実験用リポジトリ。

## 現在の段階

『インフィニティ・リミテッド 上』のページ画像で NDLOCR-Lite と PaddleOCR を比較している。原本・抽出画像・OCR結果は `.gitignore` で管理対象から外す。

この作業環境には `input/` に原本、`.venv-ndl/` と `.venv-paddle/` にそれぞれのOCR、`vendor/ndlocr-lite/` にNDLOCR-Liteのソースがある。これらはGitには含めない。

```sh
bin/extract-page.sh input/0101-20150604103726242_check.pdf 12

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

設計と進捗は [docs/progress.md](docs/progress.md) に記録する。83冊のEPUB化候補は [data/epub-candidates.tsv](data/epub-candidates.tsv) に一次選別した。

## BOOKSCAN PDF の閲覧用コピー

原本は `input/bookscan-downloads/`、閲覧用コピーは `output/readable-pdf/`。83冊の書誌候補は `data/bookscan-catalog.tsv` に記録し、元のランダム名との対応を残す。書名と著者から `書名 - 著者.pdf` を作る。書誌情報は主に表紙OCRから作った候補なので、必要に応じて原画像で校正する。

```sh
/usr/bin/python bin/prepare-books.py
```

このスクリプトは画像PDFの本文ページを判別し、紙色が強いページだけ背景を白く補正する。縦組みの列から傾きを確信できたページのみ回転する。ページごとの表示サイズが揺れないよう、余白は切らずに原本のページ枠を保持する。表紙・カラー図版の画像は再圧縮しない。PDFの基本情報には書名と著者を、XMPには翻訳者・文庫名・元ファイル名を記録する。完了冊数と処理数は `output/readable-pdf/conversion.tsv` で確認できる。

処理は途中で再実行できる。既に全ページがある出力PDFは飛ばし、ページ数が不足する試験出力は作り直す。原本はそのまま残す。
書誌を修正した本だけ作り直す場合は `--only 元ファイル名 --force` を使う。
