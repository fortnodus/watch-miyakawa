# watch-miyakawa

すし宮川の営業カレンダーで「空有り」が新しく出たら Slack に通知する監視ツール（GitHub Actions で10分ごとに実行）。

## 仕組み
- サイトのカレンダーは `https://www.sushi-miyakawa.com/src/calendar.json` を読んで描画している
- 日付ごとの `class` が `regular`=定休日 / `temporary`=臨時休業 / `reservable`=空有り
- 今日以降の `reservable` を抽出し、前回（`state.json`、Actions のキャッシュで引き継ぎ）に無かった日だけ通知
- 取得に失敗したら1回だけ「チェック失敗」、直ったら「復旧」を通知
- 標準ライブラリのみ。追加パッケージ不要

## 設定
- Secrets: `SLACK_WEBHOOK_URL`
- 手動実行: Actions → watch-miyakawa → Run workflow

## 注意
- 公開リポジトリで60日間コミットが無いと定期実行が自動停止する（Actions 画面から再開）
- サイト側の JSON の場所や形式が変わったら「チェック失敗」が届く
