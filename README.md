# watch-miyakawa

すし宮川の営業カレンダーを10分ごとに見て、「空有り」（水色の丸）が**新しく出た日**だけ Slack / メールに通知します。

## 仕組み
- Playwright でページを描画 → カレンダー部分をスクショ → 水色ピクセルの塊を検出 → 真上の日付に対応付け
- 当月と翌月（右矢印をクリック）をチェック
- 前回の結果を `state.json` に保存し、新しく出た日だけ通知（同じ空きで何度も鳴らない）
- チェック失敗時は1回だけ「失敗」通知、復旧時に「復旧」通知

## A. GitHub Actions で動かす（PC不要）
1. 新しいリポジトリを作ってこのフォルダ一式を push
   - **公開リポジトリ推奨**：10分×1か月 ≒ 4,300回実行で、private の無料枠（2,000分/月）を超えやすい。Secrets は公開でも見えません
2. Settings → Secrets and variables → Actions に登録（使う方だけ）
   - Slack: `SLACK_WEBHOOK_URL`（Slack の Incoming Webhook を作成して取得）
   - メール(Gmail例): `SMTP_HOST=smtp.gmail.com` / `SMTP_USER=自分のGmail` / `SMTP_PASS=アプリパスワード` / `MAIL_TO=通知先`
3. Actions タブ → watch-miyakawa → **Run workflow** で手動実行し、ログと `debug` 成果物のスクショを確認
   - ログに `month=12 days_found=31 hits=[]` のように出ていれば正常
   - `翌月への切替を確認できませんでした` と出たら、翌月の矢印位置の調整が必要

※ GitHub の cron は混雑時に数分〜十数分遅れることがあります。キャンセル枠を秒単位で争うならローカル実行(B)の方が確実です。

## B. 自分のPCで動かす（Windows タスクスケジューラ）
```powershell
pip install -r requirements.txt
playwright install chromium
$env:SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
$env:DEBUG="1"; python watch.py     # 動作確認
```
タスクスケジューラで「10分ごとに繰り返す」トリガーを作り、上記の環境変数をセットした .bat から `python watch.py` を実行。

## 調整ポイント（watch.py 冒頭）
- `TARGET_RGB` / `TOL`：水色の判定。サイトの色が変わったら `debug/*.png` から再測定
- 月名が取れない場合は日付が `??-18` の形で通知されます（それでも空きは分かります）
