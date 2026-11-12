# 作業メモ

- ユーザーは Memos への記事投稿をよく依頼する。投稿先は `https://memos.showway.biz/`。
- 投稿を明示的に頼まれたら、記事を用意して Memos に投稿し、保存結果を確認してリンクを返す。
- 認証には環境変数 `MEMOS_PAT` を使う。トークンの値は表示・記録しない。
- この環境では API 接続時に通常の Python User-Agent が Cloudflare の 403（code 1010）になることがある。ブラウザの User-Agent で接続できた。
- REST API は `POST /api/v1/memos` に `content` と `visibility` を送る。公開指定がなければ `PRIVATE`。投稿後は `GET /api/v1/memos/{id}` で本文を照合する。
