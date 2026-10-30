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
ruby bin/build-review.rb \
  work/sample/page-012-000.jpg \
  work/ndl/page-012/page-012-000.json \
  work/paddle/page-012/page-012-000_res.json \
  review/page-012.html
firefox-developer-edition review/page-012.html
```

PaddleOCR 3.7.0 の PP-OCRv6 では日本語の `--lang` は `ja` ではなく `japan`。各OCRのJSONは別々に保存し、比較時に座標で縦列を対応付ける。比較値は食い違いの量であり、正解率ではない。

NDLOCR-LiteのJSONと同じ場所にXMLがあれば、比較スクリプトは本文に対応しないPaddleOCR領域をルビ・ノンブルとして分類する。ページ12ではルビ「きぜん」とノンブル「10」を分類できた。

レビューHTMLは画像を埋め込んだ単独ファイル。列ごとに差を黄色で示し、列を選ぶと原本の位置を赤枠で表示する。生成物は `review/` に置き、Gitには含めない。

設計と進捗は [docs/progress.md](docs/progress.md) に記録する。
