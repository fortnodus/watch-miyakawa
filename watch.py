"""
すし宮川 営業カレンダー「空有り」監視スクリプト

サイトのカレンダーは https://www.sushi-miyakawa.com/src/calendar.json を読んで描画している。
各日付に class が付いており:
  regular    = 定休日 (オレンジ)
  temporary  = 臨時休業 (黄)
  reservable = 空有り (水色)  ← これを監視
今日以降の reservable が新しく現れたら Slack に通知する。

環境変数:
  SLACK_WEBHOOK_URL   Slack Incoming Webhook URL
  STATE_FILE          状態ファイル (既定 state.json)
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

SITE = "https://www.sushi-miyakawa.com/"
DATA_URL = SITE + "src/calendar.json"
TARGET_CLASS = "reservable"
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
JST = dt.timezone(dt.timedelta(hours=9))
WEEK = "月火水木金土日"


def fetch_calendar():
    # サイト側と同じくキャッシュ回避用のクエリを付ける
    req = urllib.request.Request(f"{DATA_URL}?{int(time.time() * 1000)}",
                                 headers={"User-Agent": "Mozilla/5.0 (watch-miyakawa)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    events = data["monthly"]
    if not isinstance(events, list) or not events:
        raise RuntimeError("calendar.json の形式が想定と違います")
    return events


def label(d: str) -> str:
    day = dt.date.fromisoformat(d)
    return f"{day.month}/{day.day}({WEEK[day.weekday()]})"


def notify(subject, body):
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    text = f"*{subject}*\n{body}"
    if not hook:
        print("[notify]", text)
        return
    req = urllib.request.Request(hook, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"open": [], "error": False}
    now = dt.datetime.now(JST)
    today = now.date().isoformat()

    try:
        events = fetch_calendar()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        if not state.get("error"):  # エラー通知は連続1回だけ
            notify("宮川監視: チェック失敗", f"{e}\n{SITE}")
            state["error"] = True
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
        sys.exit(1)

    open_dates = sorted({e["startdate"] for e in events
                         if e.get("class") == TARGET_CLASS and e.get("startdate", "") >= today})
    new = [d for d in open_dates if d not in state.get("open", [])]

    print(f"{now:%m/%d %H:%M} events={len(events)} 空有り={', '.join(open_dates) or 'なし'}")
    if new:
        notify("🍣 すし宮川に空きが出ました",
               "空有り: " + ", ".join(label(d) for d in new) +
               f"\n電話: 011-613-2221\n{SITE}\n({now:%m/%d %H:%M} 検出)")
    if state.get("error"):
        notify("宮川監視: 復旧", f"チェックが再び成功しました ({now:%m/%d %H:%M})")
    STATE_FILE.write_text(json.dumps({"open": open_dates, "error": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
