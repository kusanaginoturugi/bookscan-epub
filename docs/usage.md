# 使用の手引き

1冊を「検索可能PDF」と「ePub」にするまでの操作を、作業の順にまとめる。各段の考え方と検証の経緯は [README](../README.md) と [作業記録](progress.md) にある。ここでは、何を実行し、どこに何を保存し、いつ作り直すかだけを書く。

## 全体の流れ

```text
原本PDF ─ build-searchable-book.sh ─┬─ 検索可能PDF（output/searchable/）
                                    │   ↑ パ／バ・判断モデル・抜き取り検査のレビュー
                                    │     （保存して再実行）
                                    └─ lines.tsv ─ build-epub.sh ─ ePub（output/epub/）
                                                      ↑ ルビ検算レビュー・括弧の確認
                                                        （保存して再実行）
```

1. `bin/build-searchable-book.sh` で検索可能PDFを作る。出てきたレビュー画面で人が判定し、保存して同じコマンドを再実行する。要確認が0になるまで繰り返す。
2. `bin/build-epub.sh` で ePub を作る。ルビ検算レビューと括弧不一致の一覧を見て、直すものを保存して再実行する。
3. 本文の誤字を見つけたら `overrides.tsv` に足して、1 から作り直す（ePub の本文は検索可能PDFの文字層から作るため）。

作業名（SLUG）は `hoshi-wo-tsugu-mono` のようなローマ字の名前で、`work/searchable/SLUG/`、`work/epub/SLUG/`、`review/SLUG-*/` に使う。原本は `data/bookscan-catalog.tsv` の `source` 列のファイル名（例 `0101-20150604111029956_check.pdf`）で指定する。

## 準備

| もの | 置き場所・確認 |
|---|---|
| 原本PDF | `input/bookscan-downloads/`（`BOOKSCAN_PDF_DIR` で変更可） |
| 書誌 | `data/bookscan-catalog.tsv` に `source`・`title`・`author`・`translator` がある |
| Python | `.venv-ndl/`（GPUなら `onnxruntime-gpu[cuda,cudnn]` に入れ替える） |
| NDLOCR-Lite | `vendor/ndlocr-lite/src` |
| YomiToku | `vendor/.venv-yomitoku/bin/yomitoku` |
| ローカルLLM | llama-server（OpenAI互換）を `http://127.0.0.1:8080/v1` で起動しておく。既定モデルは gemma-4-12B（`LLM_URL`・`LLM_MODEL` で変更可） |
| AozoraEpub3 | `vendor/AozoraEpub3/AozoraEpub3.jar`。Java 21 以上が要る（`/usr/lib/jvm/java-21-openjdk` があれば自動で使う。`JAVA` で変更可） |
| 文書のHTML化 | `pandoc`、図を描くなら `mmdc`（mermaid-cli） |

作業ディレクトリ（`work/`）、完成品（`output/`）、レビュー画面（`review/`）は Git に入らない。人の判定 TSV など一部だけは `.gitignore` で名前を挙げて入れている。別のマシンへの持ち運びは README の「成果物の保管（Google Drive）」。

## 1. 検索可能PDFを作る

```sh
SOURCE=0101-20150604111029956_check.pdf
NDLOCR_DEVICE=cuda bin/build-searchable-book.sh "$SOURCE" hoshi-wo-tsugu-mono
```

- 初回は OCR（NDLOCR-Lite）→ 余白つき再認識 → YomiToku → パ／バ → 判断モデル → 文字層 → 書誌設定 → 機械処理のみの版 → 抜き取り検査、の順に全部走る。
- 最後にレビュー画面の場所と、まだ人が見ていない箇所の数が出る。
- 済んだ段は飛ばす。パ／バ・判断モデル・文字層は、入力の中身が変わったときだけ作り直す。
- 1冊は最初と同じデバイス（`cpu`/`cuda`）で通す。違うと止まる。
- 試運転は `MAX_PAGES=8` を付ける（作業ディレクトリは `SLUG-first8` になる）。

よく使う環境変数:

| 変数 | 意味 |
|---|---|
| `NDLOCR_DEVICE=cuda` | OCR・再認識・YomiToku・語の切り出しを GPU で動かす（既定 `cpu`） |
| `VOCAB_FROM=前作のSLUG` | 同じシリーズの前作のカタカナ語を「本の中の語」の規則に足す（コンマ区切りで複数） |
| `SKIP_YOMITOKU=1` / `SKIP_JUDGE=1` | その段を使わない（最初の実行で決まり、あとから変えられない） |
| `UPGRADE=1` | 古い作業ディレクトリに新しい段を通す |

### レビューして再実行する

レビュー画面は Firefox などで直接開ける。選んだら次へ自動で進む。`n` で次の未確認、`p` で戻る。

1. **パ／バ**（`review/SLUG-voicing/index.html`）を先に済ませる。辞書が変わると、判断モデルの差分の前後の文字も変わるため。
2. **判断モデル**（`review/SLUG-judge/index.html`）。LLM の答えが割れた差分だけが本体に並ぶ。
3. どちらも、画面の「保存コマンドをコピー」を押して端末に貼って実行する（`echo '…' | base64 -d > 保存先` の1行）。「TSVだけコピー」なら `wl-paste > 保存先`。
4. 同じ `build-searchable-book.sh` のコマンドをもう一度実行する。人の判定は自動判定より常に優先される。
5. 要確認が0になったら終わり。

### 作業ディレクトリに置く補正ファイル

すべて `work/searchable/SLUG/` に置く。置いたり書き換えたりしたら、`build-searchable-book.sh` を再実行すると反映される。

| ファイル | 中身 | 作り方 |
|---|---|---|
| `voicing-human.tsv` | パ／バの人の判定 | パ／バのレビュー画面から保存。前作のものを写せば判定を引き継げる |
| `judge-human.tsv` | 判断モデルの人の判定 | 判断モデルのレビュー画面から保存 |
| `overrides.tsv` | 行ごとの個別補正（`page`・`line_id`・`ocr_text`・`corrected_text`） | 手で追記するか、本文のLLM監査（後述）の画面から追記 |
| `char-map.tsv` | 本全体で置き換える字（`from`・`to`。例 `掴`→`摑`） | 手で書く |
| `skip-pages.txt` | レビューから外すページ（例 `312-322`） | 手で書く。巻末の目録は自動で外れる |
| `proofread-human.tsv` | 抜き取り検査の正解 | 抜き取り検査の画面から保存 |

```sh
printf 'from\tto\n掴\t摑\n' > work/searchable/SLUG/char-map.tsv
```

### 抜き取り検査（精度の見積もり）

`review/SLUG-proofread/index.html` で、無作為に選んだ200行を原本と照らす。原本と同じ候補をクリックし、どれも違えば書き、行の切り出しがおかしければ除外する。保存して再実行すると、段階ごとの正解率が `work/searchable/SLUG/proofread-score.txt` に出る。選ぶ行は `sample.tsv` に固定され、作り直しても変わらない。

## 2. ePub を作る

```sh
bin/build-epub.sh "$SOURCE" hoshi-wo-tsugu-mono
# 巻末の奥付や広告を外すとき
bin/build-epub.sh 0101-20150604091458299_check.pdf kyojin-tachi-no-hoshi --end-page 471
```

- 中では、ルビの抽出（`extract-ruby.py`）→ 青空文庫形式のテキスト（`build-aozora-text.py`）→ AozoraEpub3 の順に動く。
- 完成品は `output/epub/書名 - 著者.epub`、途中のテキストは `work/epub/SLUG/書名 - 著者.txt`、表紙は原本1ページ目から `cover.jpg` を作る。
- 3番目以降の引数は `build-aozora-text.py` に渡る。`--start-page`・`--end-page` で本文に入れるページを絞る。奥付や出版社の広告目録が入ると、括弧不一致が大量に出る（『巨人たちの星』は 226件 → 471ページまでに絞って6件）。
- ルビの抽出には、その本の NDLOCR-Lite の結果（`work/searchable/SLUG/ocr/*.xml`）が**全ページ分**要る。消えていたり一部しかなかったりすると、抽出が止まるか、そのページのルビが抜ける。ルビを作り直さずに本文だけ作り直すなら `SKIP_RUBY=1` を付ける（既存の `ruby.tsv` は使われない点に注意）。
- `ruby.tsv` は箇所ごとにキャッシュされ、再実行では聞き直さない。

### 括弧不一致を確認する

`build-aozora-text.py` は、書き出したテキストで括弧（「」『』（）〈〉【】など）の対応が取れない行を最後に表示する。

```text
L1228: unclosed 〈 | ...が引き揚げられて〈シャピアロンＶ号はもう飛び...
L1421: mismatch close 」 (top=None) | ...　知るものかね」...
```

`L` の数字はテキストファイルの行番号。原本を見て、OCR の誤りなら `overrides.tsv` に補正を足して検索可能PDFから作り直す。原本どおり（会話が段落をまたぐなど）なら、そのままでよい。

### ルビ検算レビュー

抽出したルビをローカルLLMで検算し、原本の2倍の切り出しつきで確認する画面を作る。

```sh
S=hoshi-wo-tsugu-mono
.venv-ndl/bin/python bin/audit-ruby.py work/searchable/$S work/epub/$S/ruby.tsv \
  work/epub/$S/ruby-audit.tsv review/$S-ruby/index.html \
  --source "$SOURCE" --human work/epub/$S/ruby-human.tsv --title "星を継ぐもの ルビ検算"
```

- LLM が怪しいとした箇所と、親文字にひらがなが混じる・ルビの縦幅が親文字に足りないなど機械的に怪しい箇所が並ぶ。
- 選んだら「保存コマンドをコピー」で `work/epub/SLUG/ruby-human.tsv` に保存し、`build-epub.sh` を再実行する（`ruby-human.tsv` があれば自動で使う）。
- 画面だけ作り直すなら `--from-audit`（LLMに聞き直さない）。

## 3. 本文の LLM 監査（任意）

人のレビューが済んだあとも残る誤字を、ローカルLLMにページ単位で探させ、疑わしい箇所を VLM（画像）と `margin.tsv` の別の読みで確かめる。結果は `overrides.tsv` に追記する形で出る。

```sh
S=hoshi-wo-tsugu-mono
.venv-ndl/bin/python bin/audit-text-llm.py input/bookscan-downloads/$SOURCE \
  work/searchable/$S/lines.tsv review/$S-llm-audit/index.html \
  --margin-tsv work/searchable/$S/margin.tsv --overrides work/searchable/$S/overrides.tsv
```

- `--pages 205-224` でページを絞れる。巻末の目録は自動で外れる（`--include-catalog` で含める、`--skip-pages` で追加）。
- 結果は `work/searchable/SLUG/llm-audit.tsv` に残る。`--reuse` で LLM に聞かずに画面だけ作り直す。
- 画面で採るものを選び、出てきた追記コマンドを端末で実行すると `overrides.tsv` に足される。そのあと `build-searchable-book.sh` → `build-epub.sh` の順に再実行する。

## 4. 文書を更新する

README や作業記録、この手引きを書き換えたら、HTML を作り直す。

```sh
bin/build-docs-html.sh
```

成果物の一覧は `artifacts.md`（→ `artifacts.html`）。新しい本やレビュー画面を作ったら、ここに足す。

## 困ったとき

| 症状 | 対処 |
|---|---|
| `not in catalog: …` | `data/bookscan-catalog.tsv` の `source` 列と同じファイル名を渡す |
| `no LLM server at …` | llama-server を起動するか、`LLM_URL` を合わせる。使わないなら `SKIP_JUDGE=1`（最初の実行のみ） |
| `OCR fell back to the CPU` | GPU が使えていない。`.venv-ndl` の `onnxruntime-gpu` と CUDA を確認する |
| デバイスが違うと言われて止まる | 作業ディレクトリの `device` に記録したデバイスで実行する |
| 判断モデルの段が止まる（どの差分にも当たらない人の判定） | パ／バの辞書が変わって前後の文字がずれた。判断モデルの画面を作り直して判定し直す |
| `build-epub.sh` が `extract-ruby.py` の `IndexError` で止まる | `work/searchable/SLUG/ocr/` が空。OCR をやり直すか、`SKIP_RUBY=1` で本文だけ作る |
| AozoraEpub3 が `UnsupportedClassVersionError` | Java 21 以上を `JAVA=…/bin/java` で指定する |
| 括弧不一致が数百件出る | 奥付や広告ページが本文に入っている。`--end-page` で絞る |
