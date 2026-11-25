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
