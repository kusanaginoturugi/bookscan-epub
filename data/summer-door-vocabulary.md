# 『夏への扉』全編の語句一覧

対象は `output/` に置いた注釈試作PDFの本文、PDFページ7〜338（332ページ）。1〜6ページの表紙・献辞と、339〜356ページの訳者あとがき・広告などは含めない。3分割PDFのページ番号は通し番号に直した。

- [全語一覧](summer-door-all-words.tsv): ローカルのSudachiで本文を形態素解析した8,496語。動詞・形容詞は辞書形にし、助詞・助動詞も含めた。`occurrences` は出現回数、`pages` はPDFページ番号。句読点と記号は含めない。
- [読書用の修正版](summer-door-reading-term-candidates_fix.tsv): ユーザーが不要語を削った234語。今後はこちらを参照する。初出ページ順。うち12語は[既存の注釈候補](summer-door-note-candidates.md)に出典があり、7語は同ファイル末尾のユーザー追記、215語はローカルLLMの未検証候補。
- [読書用の説明一覧](summer-door-reading-glossary.tsv): 修正版234語から、物語の中だけの人名・組織・発明・言い回しや、OCRの連結・重複・一般語を[除外一覧](summer-door-glossary-drops.tsv)で外し、[追加分](summer-door-glossary-additions.tsv)（早口言葉「ピイタア・パイパア」）を足した137語。説明はすべて既存の出典付き説明か[補正一覧](summer-door-glossary-corrections.tsv)のもので、ローカルLLMの下書きは残っていない。実在の語には主に英語版Wikipediaへの出典リンクを付けた。`note_source` は説明の由来、`review_status` は元の候補語の確認状況を示す。
- [削除前のLLM候補](summer-door-reading-term-candidates.tsv): 本文をローカルのGemma 4 E4Bに通し、1956年以前に存在した現実の人物・場所・制度・技術・作品を意識して抽出した497語。比較用に残す。

読書用PDFは `output/夏への扉 - 用語注釈版.pdf`（Git管理外）。`bin/annotate/generate-summer-door-glossary.py` が説明一覧を作り、`bin/annotate/build-summer-door-annotated-pdf.py` が3分割PDFを結合して初出ページの上余白に注釈を付ける。後者には `pypdf` と `reportlab` が必要。

1957年以降の語を厳密に除くには、一語ずつ初出年代を資料で確認する必要がある。修正版にも年代未検証の語やOCRの誤読が残り得る。年代を確認済みの一覧としては使わない。全語一覧にもOCRや形態素解析の誤りが残る。
