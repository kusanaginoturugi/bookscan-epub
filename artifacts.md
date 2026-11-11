# 検索可能PDFとレビューの目次

このページは、このリポジトリで作った成果物への入口。PDF、OCR結果、レビューの一部はGit管理外のローカルファイルなので、同じ作業ディレクトリで開く。`output/` のPDFはGoogle Driveの `bookscan-epub/output/` にも置いてある。リンクが切れていたら、[README](README.md)の「成果物の保管」の手順で `rclone copy` して戻す。完成品と試験版のPDFは、ページ単位の奇数ページ見開き（`/PageLayout /TwoPageLeft`）で、1ページ目をページ全体に合わせて開く（`/OpenAction` の `/Fit`）設定を持つ。Firefoxでの効き方は[作業記録](docs/progress.md)のPDFの初期表示設定を参照。

## 傾き補正込みの4冊（2026-11-10）

`bin/build-searchable-book.sh` で作成。各冊とも、辞書補正なしの完成品と、ローカルLLMで作った濁点・半濁点の辞書で補正した版がある。辞書の判定と見送り理由は `work/searchable/作業名/variant-glossary.tsv`。

| 書名 | ページ | 傾き補正 | 完成品 | 辞書補正版 | 辞書 |
|---|---|---|---|---|---|
| 時間泥棒 | 196 | 0 | [PDF](output/searchable/時間泥棒%20-%20ジェイムズ・P・ホーガン.pdf) | [PDF](output/searchable/時間泥棒%20-%20ジェイムズ・P・ホーガン%20辞書補正.pdf) | [8語10か所](work/searchable/jikan-dorobou/variant-glossary.tsv) |
| 暗黒星雲突破！ | 216 | 0 | [PDF](output/searchable/暗黒星雲突破！%20-%20A・B・チャンドラー.pdf) | [PDF](output/searchable/暗黒星雲突破！%20-%20A・B・チャンドラー%20辞書補正.pdf) | [5語11か所](work/searchable/ankoku-seiun-toppa/variant-glossary.tsv) |
| 天の光はすべて星 | 284 | 1 | [PDF](output/searchable/天の光はすべて星%20-%20フレドリック・ブラウン.pdf) | [PDF](output/searchable/天の光はすべて星%20-%20フレドリック・ブラウン%20辞書補正.pdf) | [5語7か所](work/searchable/ten-no-hikari/variant-glossary.tsv) |
| インフィニティ・リミテッド（下） | 388 | 3 | [PDF](output/searchable/インフィニティ・リミテッド（下）%20-%20ジェイムズ・P・ホーガン.pdf) | [PDF](output/searchable/インフィニティ・リミテッド（下）%20-%20ジェイムズ・P・ホーガン%20辞書補正.pdf) | [20語61か所](work/searchable/infinity-lower/variant-glossary.tsv) |

## 濁点・半濁点のレビュー（2026-11-11）

『夏への扉』の [濁点・半濁点レビュー](review/summer-door-voicing/index.html)。濁点・半濁点1字だけが違うカタカナ語52組を、原本の縮小画像・行・3倍拡大で確かめて選ぶ。選んだ結果は画面の下のTSVにまとまり、`--fix-variants` の辞書になる。YomiTokuの全ページの結果は `work/voicing/summer-door/yomitoku/`。

## OCRのCPUとGPUの比較（2026-11-11）

虎よ・インフィニティ上巻・夏への扉から141ページを抜き出し、NDLOCR-LiteをCPUとCUDAで比べた。精度は同等で、速さは約3倍。詳細は[作業記録](docs/progress.md)の「GPU（CUDA）でのOCRと余白つき再認識」。

| 種類 | ファイル | 内容 |
|---|---|---|
| 標本 | [manifest.json](work/ocr-engine-check/manifest.json) | 冊ごとの `subset.pdf` のページと、正解行93行 |
| `ocr.py` の比較 | [report-ocr.txt](work/ocr-engine-check/report-ocr.txt) | 保存分・CPU・CUDA 2回。食い違い43行の一覧つき |
| 本番の文字列の比較 | [report-margin.txt](work/ocr-engine-check/report-margin.txt) | 余白つき再認識を含む。正解行の誤り CPU 14/3300、CUDA 13/3300（括弧の補正前は20、19） |
| YomiTokuとの比較 | [report-engines.txt](work/ocr-engine-check/report-engines.txt) | 正規化と部分文字列で採点。正解行の誤り 本番NDL 14〜15/3374、YomiToku 94/3374。無作為ページの食い違い130か所の一覧つき（画像確認ではNDLが正しい90、YomiTokuが正しい28、両方誤り12） |
| 括弧の補正後 | [report-engines-brackets.txt](work/ocr-engine-check/report-engines-brackets.txt) | `--fix-brackets` を当てた本番NDL。正解行の誤り CPU 9、CUDA 8/3374。YomiTokuとの食い違い124行 |

## 夏への扉

| 種類 | ファイル | 内容 |
|---|---|---|
| 完成品 | [検索可能PDF](output/trial/夏への扉%20-%20ロバート・A・ハインライン.pdf) | 356ページ。高さ400px以上の縦書き行は余白30pxの再認識、短い行は保存済みOCR。ダッシュ補正と、辞書による濁点・半濁点の補正（38か所）あり |
| 括弧補正版 | [検索可能PDF（括弧補正）](output/trial/夏への扉%20-%20ロバート・A・ハインライン%20括弧補正.pdf)・[文字層版](output/trial/summer-door-text-unfinalized-brackets.pdf) | 完成品と同じ手順に `--fix-brackets` を追加。〝〟92組と〈〉43個を直し、15・16ページの `"` は残した。完成品との違いは括弧と引用符だけ。置き換えは未決定 |
| 人手辞書版 | [検索可能PDF（括弧補正・人手辞書）](output/trial/夏への扉%20-%20ロバート・A・ハインライン%20括弧補正%20人手辞書.pdf)・[辞書](work/eval/summer-door-voicing-glossary.tsv) | 括弧補正版の辞書を、[レビュー画面](review/summer-door-voicing/index.html)でユーザーが確定した52語に替えた。置き換えは40種69か所（従来の辞書は13種38か所）。括弧補正版との違いは、濁点・半濁点の31か所だけ。置き換えは未決定 |
| 注釈試作 | [薄色・右開き・注釈入りPDF](output/trial/夏への扉%20-%20ロバート・A・ハインライン%20注釈試作%20Firefox版%20薄色右開き.pdf) | PDFページ21・23・24に注釈と出典リンク。本文の偶数ページ176枚の端線を修正。Readest用の右開き指定入り。追加の[語句候補](data/summer-door-note-candidates.md) |
| 再認識 | [余白別認識結果](work/margin/summer-door.tsv)・[実行ログ](work/margin/summer-door.log) | 全6181行。保存済みOCRと余白0・8・14・20・30pxの読み |
| 正解 | [無作為35行](work/margin/summer-door-gold-random.tsv) | 読みが変わる群15行、同じ群10行、ルビに近い行10行。完成品の編集差2 |
| 辞書 | [画像確認済み15語](work/eval/summer-door-glossary.tsv)・[頻出語候補332語](work/eval/summer-door-glossary-candidates.tsv) | 確認済みの語だけを `--fix-variants` に使う |
| 中間PDF | [画像保持版](output/trial/summer-door-sanitized.pdf)・[原本OCR版](output/trial/summer-door-original-ocr.pdf)・[文字層版（辞書補正前）](output/trial/summer-door-text-unfinalized.pdf)・[文字層版](output/trial/summer-door-text-unfinalized-v2.pdf) | 作業確認用。閲覧には完成品を使う |

OCRのページ別JSON・TXT・XMLは `work/trial/summer-door-ocr/` に保存してある。

## インフィニティ・リミテッド（上）

| 種類 | ファイル | 内容 |
|---|---|---|
| 完成品 | [検索可能PDF](output/trial/インフィニティ・リミテッド（上）%20-%20ジェイムズ・P・ホーガン.pdf) | 388ページ、透明テキスト層付き |
| 試験版 | [余白30pxの文字層 v2](output/trial/インフィニティ・リミテッド（上）%20-%20ジェイムズ・P・ホーガン%20v2.pdf) | 認識モデルを余白30pxで再実行し、ダッシュを補正。無作為23行の編集差58→7。短い行の閉じかぎ括弧が33行で落ちる（`--min-height` 導入前）。置き換えは未決定 |
| 括弧補正版 | [v2 括弧補正](output/trial/インフィニティ・リミテッド（上）%20-%20ジェイムズ・P・ホーガン%20v2%20括弧補正.pdf)・[文字層版](output/trial/infinity-upper-text-unfinalized-v2-brackets.pdf) | v2と同じ手順に `--fix-brackets` を追加。〝〟134個と〈〉90個を直した。v2との違いは括弧と引用符だけ。置き換えは未決定 |
| 評価（完成品） | [ページ画像と文字層のレビュー](review/infinity-upper-eval/index.html) | 通常10ページ、例外ページ、空白ページの画像。v2は対象外 |
| 評価（完成品） | [集計JSON](review/infinity-upper-eval/report.json)・[標本TSV](review/infinity-upper-eval/sample.tsv) | 文字層一致、検索語13件、標本一覧。4ページ・行10の補正を直した後に再実行すると、4ページが不一致になる（完成品は直す前の補正のまま） |
| 辞書 | [画像確認済み13語](work/eval/infinity-upper-glossary.tsv) | 補正に使う表記 |
| 辞書 | [頻出語候補517語](work/eval/infinity-upper-glossary-candidates.tsv)・[上位30語の画像](review/infinity-upper-glossary/index.html) | 未確認候補は自動補正に使わない |
| 補正 | [画像確認済みの7行](work/eval/infinity-upper-overrides.tsv)（4ページ・行10はv2用に修正済み）・[検索語13件](work/eval/infinity-upper-queries.tsv) | 元OCRを残して文字層だけ補正 |
| 文章レビュー | [画像付きレビュー](review/infinity-upper-llm/index.html)・[採点TSV](review/infinity-upper-llm/ranked.tsv) | ローカルLLMで選んだ20行。スコアは正誤判定ではない |
| 中間PDF | [画像保持版](output/trial/infinity-upper-sanitized.pdf)・[原本OCR版](output/trial/infinity-upper-original-ocr.pdf)・[文字層版](output/trial/infinity-upper-text-unfinalized.pdf)・[v2文字層版](output/trial/infinity-upper-text-unfinalized-v2.pdf) | 作業確認用。閲覧には完成品を使う |

OCRのページ別JSON・TXT・XMLは `work/trial/infinity-upper-ocr/` に保存してある。評価結果と手順は[作業記録](docs/progress.md)を参照。

### 行画像の余白と再認識

| 種類 | ファイル | 内容 |
|---|---|---|
| 再認識 | [全ページの余白別認識結果](work/margin/infinity-upper.tsv) | 全6621行。保存済みOCRと余白0・8・14・20・30pxの読み。v2の文字層の元 |
| 正解 | [無作為23行](work/margin/gold-random.tsv)・[既存の20行](work/margin/gold-existing.tsv) | 原本画像から転記。`bin/margin-recognize.py --score` で採点 |
| 感度実験 | [条件別の認識結果](work/ocr-sensitivity/variants.tsv)・[余白の比較](work/ocr-sensitivity/pad-sweep.tsv)・[集計JSON](work/ocr-sensitivity/summary.json)・[正解行](work/ocr-sensitivity/gold-lines.tsv) | 傾き補正比較の6ページで、枠の幅・揺らし・JPEG品質・余白を変えて認識モデルだけ再実行 |

### 傾き補正の比較

| 種類 | ファイル | 内容 |
|---|---|---|
| OCR比較 | [画像と正解テキストのレビュー](work/deskew-study/index.html)・[集計JSON](work/deskew-study/report.json)・[正解TSV](work/deskew-study/gold.tsv) | 6ページ、13行、488文字の原本・再保存・傾き補正後比較 |
| 座標検証 | [補正後画像＋補正前OCRの文字層](work/deskew-study/transformed-ocr.pdf)・[青字での位置確認用PDF](work/deskew-study/transformed-visible.pdf)・[検索位置の画像](work/deskew-study/search-hit-35.jpg) | 文字層を画像と同じ角度だけ回転 |
| 中間データ | [原本ページ抜粋](work/deskew-study/original.pdf)・[再保存のみ](work/deskew-study/reencoded.pdf)・[補正後ページ抜粋](work/deskew-study/rotated.pdf)・[対応表](work/deskew-study/pages.tsv) | 35、160、179、192、209、210ページ |

比較の再実行には `../bookscan/vendor/.venv-ndlocr/bin/python bin/deskew-study.py --score` を使う。

## 虎よ、虎よ！

| 種類 | ファイル | 内容 |
|---|---|---|
| 完成品 | [検索可能PDF（空白修正）](output/trial/虎よ、虎よ！%20-%20アルフレッド・ベスター%20空白修正.pdf) | 364ページ、透明テキスト層付き |
| 試験版 | [空白修正 v2](output/trial/虎よ、虎よ！%20-%20アルフレッド・ベスター%20空白修正%20v2.pdf)・[評価](review/tiger-eval-v2/index.html) | 15ページ・行9、254ページ・行4の個別補正を追加。正解15行の編集差3、検索語7/12は完成品と同じ。置き換えは未決定 |
| 括弧補正版 | [空白修正 v2 括弧補正](output/trial/虎よ、虎よ！%20-%20アルフレッド・ベスター%20空白修正%20v2%20括弧補正.pdf)・[文字層版](output/trial/tiger-text-normalized-v2-brackets.pdf) | 空白修正 v2と同じ手順に `--fix-brackets` を追加。〝〟21個と〈〉8個を直した。v2との違いは括弧と引用符だけ。置き換えは未決定 |
| 評価 | [ページ画像と文字層のレビュー](review/tiger-eval/index.html)・[集計JSON](review/tiger-eval/report.json) | 通常・例外ページ、検索語の照合 |
| 辞書 | [確認済み語](work/eval/tiger-glossary.tsv)・[頻出語候補](work/eval/tiger-glossary-candidates.tsv) | 候補は未確認 |
| 補正 | [個別補正](work/eval/tiger-overrides.tsv)・[画像照合した正解行](work/eval/tiger-gold.tsv)・[検索語](work/eval/tiger-queries.tsv) | 原本画像で確認した内容 |
| 文章レビュー | [画像付きレビュー](review/tiger-llm/index.html)・[採点TSV](review/tiger-llm/ranked.tsv) | ローカルLLMによる優先順位 |

## プロジェクト資料

- [README](README.md)：方針、再現手順、各スクリプトの説明
- [作業記録](docs/progress.md)：評価結果、制約、次の作業
- [書誌カタログ](data/bookscan-catalog.tsv)：元PDF名と書名の対応

HTMLは `pandoc --standalone --toc -M lang=ja -M title=検索可能PDFとレビューの目次 -o artifacts.html artifacts.md` で再生成できる。
