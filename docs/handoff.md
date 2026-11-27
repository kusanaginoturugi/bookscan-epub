# 引き継ぎ

## 『夏への扉』読書用PDF（2026-11-05）

- 最新版: `output/trial/夏への扉 - ロバート・A・ハインライン 注釈試作 Firefox版 薄色右開き.pdf`（Git管理外）。PDFページ21・23・24だけに既存の注釈と出典リンクがある。
- 追加候補14語は `data/summer-door-note-candidates.md`。まだPDFには入れていない。ユーザーが全編を読んでから語句選びを再開する。
- 生成手順は `README.md` の「『夏への扉』の読書用注釈試作」。素材は `data/summer-door-reading-notes.json`、スクリプトは `bin/annotate/embed-reading-notes.py`、`bin/annotate/mask-scan-edge.py`、`bin/annotate/set-pdf-direction.py`。
- 現行PDFはFirefox向けの淡い注釈アイコンと別の出典リンク、偶数ページの端線隠し、Readest向けの右開き指定を含む。Firefoxでのリンク動作とReadestの自動見開き方向は、実画面で最終確認が済んでいない。
- パーシング・スクエアは会話で挙がったが、候補には未登録。スタンダードは原文でも法人名が特定できない。

## 検索可能PDFの精度向上と全8冊反映（2026-11-25）

- 精度向上の検討項目1〜6（[docs/work-plan.md](file:///home/onoue/src/bookscan-epub/docs/work-plan.md)）をすべて検証・実装し、全8冊へ反映済み。
  - 既レビュー6冊（『星を継ぐもの』『ガニメデの優しい巨人』『巨人たちの星』『内なる宇宙（上・下）』『プロテウス・オペレーション（上）』）は追加レビュー含め全件確定（要確認 0か所）。
  - 機械処理のみの抜き取り検査スコアは、『星を継ぐもの』100.00%（200/200行）、『プロテウス・オペレーション（上）』99.99%（199/200行）。
  - 未レビューの2冊（『造物主の掟』『造物主の選択』）も要確認がそれぞれ 43か所・22か所まで減少。
- 次のフェーズは [docs/epub-plan.md](file:///home/onoue/src/bookscan-epub/docs/epub-plan.md) に沿った青空文庫形式→AozoraEpub3 による ePub 化。

## ePub化とルビ修正・roborev設定（2026-11-26）

- `bin/build-epub.sh`（`extract-ruby.py` → `build-aozora-text.py` → `AozoraEpub3`）およびルビ検算レビュー（`bin/audit-ruby.py`）を実装し、『星を継ぐもの』の ePub 3（`output/epub/星を継ぐもの - ジェイムズ・P・ホーガン.epub`、Git管理外、835ルビ）を生成した。
- 複合語や前後の助詞・漢字まで親文字が広がる誤り（例: `潜り抜《くぐ》`、`粘り強《ねば》`、`異星文明局《エイリアン》`）への対策を `extract-ruby.py`・`audit-ruby.py`・`build-aozora-text.py` へ反映し、『星を継ぐもの』の該当14箇所も元画像確認のうえ `work/epub/hoshi-wo-tsugu-mono/ruby-human.tsv` で修正・再生成済み。
- `prepare-trial-pdf.py`（全本文ページの背景クリーニング、傾き閾値 1.15、外周端線マスク）と `overrides.tsv` 対応（`build-searchable-book.sh` / `judge-ocr-disagreements.py`）も反映済み。
- `roborev` は `.roborev.toml`（書籍由来 TSV・`work/**`・`eval/**` を差分から除外）、`.gitignore`（`/.roborev/`）、git フック（`post-commit` / `pre-push`）、エージェントスキル・フック（Claude / Codex / Gemini）まで設定済み。グローバル設定（`~/.roborev/config.toml`）は `review_agent = 'claude-code'` / `review_backup_agent = 'gemini'` のクロスレビュー構成。`~/.gemini/config/hooks.json` に `Stop` フック（`roborev-dirty-review`・`roborev-auto-fix`）を導入済み。
- 通読フィードバックを受けて、行またぎ略語空白（`Ｎ ＡＳＡ`）、行頭縦中横箇条書き記号の VLM 再認識（`judge-ocr-disagreements.py` の `tcy-marker`）と縦中横出力、型番コードの全角正立化（`ＭＢ—３０７６／Ｋ２／Ｅ`）、`margin-outlier` の `margin14 == margin20 != margin30` 緩和＋`unpaired`/`misaligned` 対応（`clement`→`element` 自動修正）、ルビ抽出の位置 `⟦⟧` マーカー、括弧不一致チェッカー（`build-aozora-text.py`）を実装済み。
- 『ガニメデの優しい巨人』も ePub 3（`output/epub/ガニメデの優しい巨人 - ジェイムズ・P・ホーガン.epub`、454 ルビ、1〜23 章）とルビ検算レビュー（`review/ganymede-no-yasashii-kyojin-ruby/index.html`、87 件）を生成済み。
- 2026-11-27：『ガニメデ』のルビ検算と本文補正を反映して ePub 3 を再生成した（453 ルビ、括弧不一致は原本どおりの1件のみ）。『巨人たちの星』は 2026-11-26 に別のマシンで ePub 化したが、そこでは NDLOCR-Lite の結果が 1〜34 ページ分しかなくルビが 54 か所だけ。gallsk には全ページの OCR 結果があるので作り直す。次は『巨人たちの星』の作り直しと、残り 3 冊（『内なる宇宙（上・下）』『プロテウス・オペレーション（上）』）の ePub 化（`docs/work-plan.md` の ePub化 9）。
