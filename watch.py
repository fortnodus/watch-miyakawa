"""
すし宮川 営業カレンダー「空有り」(水色の丸) 監視スクリプト

- Playwright でページを実際に描画し、カレンダー部分をスクリーンショット
- 水色 (#88CCF1 付近) の丸を画素で検出 → 一番近い日付の数字に割り当て
- 当月と翌月 (右矢印クリック) の両方をチェック
- 前回から「新しく空有りになった日」だけ Slack / メールに通知

環境変数:
  SLACK_WEBHOOK_URL   Slack Incoming Webhook URL (任意)
  SMTP_HOST, SMTP_PORT(既定465), SMTP_USER, SMTP_PASS, MAIL_TO   メール通知 (任意)
  STATE_FILE          状態ファイル (既定 state.json)
  DEBUG=1             スクリーンショットを debug/ に保存し検出結果を詳しく出力
"""
import asyncio, io, json, os, re, smtplib, sys, datetime as dt
from email.mime.text import MIMEText
from pathlib import Path
import urllib.request

from PIL import Image
from playwright.async_api import async_playwright

URL = "https://www.sushi-miyakawa.com/"
TARGET_RGB = (136, 204, 241)   # 空有り の水色 (スクショ実測)
TOL = 40                       # 色の許容差 (各チャンネル)
MIN_BLOB_PX = 30               # これ未満の塊はノイズ扱い
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
DEBUG = os.environ.get("DEBUG") == "1"
JST = dt.timezone(dt.timedelta(hours=9))
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
          "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]

# ---------- ページ側で実行する JS ----------
# 「空有り」の凡例から祖先をたどり、日付(1〜31)を28個以上含む要素をカレンダー本体とみなす
FIND_CAL_JS = r"""
() => {
  const all = [...document.querySelectorAll('body *')];
  const legend = all.filter(e => e.children.length === 0 && e.textContent.trim() === '空有り')
                    .find(e => e.getBoundingClientRect().width > 0);
  if (!legend) return null;
  const dayEls = root => [...root.querySelectorAll('*')].filter(e =>
      e.children.length === 0 && /^([1-9]|[12][0-9]|3[01])$/.test(e.textContent.trim()) &&
      e.getBoundingClientRect().width > 0);
  let cal = legend.parentElement;
  while (cal && dayEls(cal).length < 28) cal = cal.parentElement;
  if (!cal) return null;
  document.querySelectorAll('[data-miya-cal]').forEach(e => e.removeAttribute('data-miya-cal'));
  cal.setAttribute('data-miya-cal', '1');
  cal.scrollIntoView({block: 'start'});
  const cb = cal.getBoundingClientRect();
  const rel = r => ({x: r.left - cb.left, y: r.top - cb.top, w: r.width, h: r.height});
  const days = dayEls(cal).map(e => ({d: +e.textContent.trim(), ...rel(e.getBoundingClientRect())}));
  // 凡例行全体 (水色の丸を含む) を除外領域にする
  let lg = legend; for (let i = 0; i < 3 && lg.parentElement !== cal; i++) lg = lg.parentElement;
  const text = cal.innerText;
  // 右上の矢印候補: カレンダー上部25%で一番右にあるクリック可能っぽい要素
  const clickable = [...cal.querySelectorAll('a,button,[onclick],[class*=next],[class*=arrow],span,div,svg,img')]
    .map(e => ({e, r: e.getBoundingClientRect()}))
    .filter(o => o.r.width > 10 && o.r.width < 150 && o.r.top - cb.top < cb.height * 0.25 &&
                 o.r.left - cb.left > cb.width * 0.7 && !/\d/.test(o.e.textContent));
  clickable.sort((a, b) => (b.r.left - a.r.left) || (a.r.width * a.r.height - b.r.width * b.r.height));
  const nx = clickable[0];
  window.__miyaCal = cal;
  return {
    box: {x: cb.left + scrollX, y: cb.top + scrollY, w: cb.width, h: cb.height},
    legend: rel(lg.getBoundingClientRect()),
    days, text,
    next: nx ? {x: nx.r.left + nx.r.width / 2, y: nx.r.top + nx.r.height / 2} : null,
  };
}
"""


def find_blobs(img: Image.Image):
    """水色ピクセルの連結成分を返す [(cx, cy, size)]"""
    img = img.convert("RGB")
    W, H = img.size
    px = img.load()
    mask = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            if (abs(r - TARGET_RGB[0]) <= TOL and abs(g - TARGET_RGB[1]) <= TOL
                    and abs(b - TARGET_RGB[2]) <= TOL and b > r + 50):
                mask[y][x] = True
    seen = [[False] * W for _ in range(H)]
    blobs = []
    for y in range(H):
        for x in range(W):
            if mask[y][x] and not seen[y][x]:
                stack, pts = [(x, y)], []
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    pts.append((cx, cy))
                    for nx, ny in ((cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)):
                        if 0 <= nx < W and 0 <= ny < H and mask[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                if len(pts) >= MIN_BLOB_PX:
                    blobs.append((sum(p[0] for p in pts) / len(pts),
                                  sum(p[1] for p in pts) / len(pts), len(pts)))
    return blobs


def nearest_day(bx, by, days):
    """丸の真上(やや上)にある日付数字を探す"""
    best, best_d = None, 1e9
    for d in days:
        cx, cy = d["x"] + d["w"] / 2, d["y"] + d["h"] / 2
        if cy > by + 5:          # 数字は丸より上にある
            continue
        dist = abs(cx - bx) * 2 + (by - cy)
        if dist < best_d:
            best, best_d = d["d"], dist
    return best


def parse_month(text: str):
    up = text.upper()
    for i, m in enumerate(MONTHS):
        if m in up:
            return i + 1
    return None


async def scan_view(page, scale, tag):
    info = await page.evaluate(FIND_CAL_JS)
    if not info:
        raise RuntimeError("カレンダー要素が見つかりません (サイト構造が変わった可能性)")
    await page.wait_for_timeout(500)
    # 要素そのものを撮る (はみ出し・スクロール位置は Playwright が処理)
    png = await page.locator('[data-miya-cal="1"]').first.screenshot()
    img = Image.open(io.BytesIO(png))
    if DEBUG:
        print(f"[{tag}] calendar box={info['box']} image={img.size}")
    if DEBUG:
        Path("debug").mkdir(exist_ok=True)
        img.save(f"debug/{tag}.png")
    lg = info["legend"]
    hits = []
    for bx, by, size in find_blobs(img):
        x, y = bx / scale, by / scale
        if lg["y"] - 10 <= y <= lg["y"] + lg["h"] + 10:   # 凡例の水色丸は除外
            continue
        hits.append((nearest_day(x, y, info["days"]), round(x), round(y), size))
    month = parse_month(info["text"])
    if DEBUG:
        print(f"[{tag}] month={month} days_found={len(info['days'])} hits={hits} next={info['next']}")
    return month, [h[0] for h in hits if h[0]], info


async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 430, "height": 932},
                                        device_scale_factor=2, locale="ja-JP")
        page = await ctx.new_page()
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(1500)

        try:
            return await _scan_all(page)
        except Exception:
            # 失敗時はページ全体を残して原因調査できるようにする
            Path("debug").mkdir(exist_ok=True)
            await page.screenshot(path="debug/fail_fullpage.png", full_page=True)
            Path("debug/fail_page.html").write_text(await page.content(), encoding="utf-8")
            raise
        finally:
            await browser.close()


async def _scan_all(page):
    results = {}
    m1, d1, info = await scan_view(page, 2, "view1")
    results[m1] = d1
    if info["next"]:
        # 矢印の座標は scan_view 内のスクロール後の値なので、同じ位置に戻してからクリック
        await page.evaluate("document.querySelector('[data-miya-cal]').scrollIntoView({block:'start'})")
        info2 = await page.evaluate(FIND_CAL_JS)
        nx = (info2 or info)["next"]
        await page.mouse.click(nx["x"], nx["y"])
        await page.wait_for_timeout(1500)
        m2, d2, _ = await scan_view(page, 2, "view2")
        if m2 and m2 != m1:
            results[m2] = d2
        elif DEBUG:
            print("翌月への切替を確認できませんでした (矢印の位置要確認)")

    today = dt.datetime.now(JST).date()
    dates = set()
    for month, days in results.items():
        if month is None:
            # 月名が取れなくても通知はしたいので仮キー
            dates.update(f"??-{d:02d}" for d in days)
            continue
        year = today.year + (1 if month < today.month else 0)
        dates.update(f"{year}-{month:02d}-{d:02d}" for d in days)
    return sorted(dates), sorted(k for k in results if k)


# ---------- 通知 ----------
def notify(subject, body):
    sent = False
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook:
        req = urllib.request.Request(hook, data=json.dumps({"text": f"*{subject}*\n{body}"}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        sent = True
    if os.environ.get("SMTP_HOST") and os.environ.get("MAIL_TO"):
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_USER"], os.environ["MAIL_TO"]
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 465)), timeout=20) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
        sent = True
    if not sent:
        print("[notify]", subject, body)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"open": [], "error": False}
    try:
        dates, months = asyncio.run(check())
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        if not state.get("error"):           # エラー通知は連続1回だけ
            notify("宮川監視: チェック失敗", f"{e}\n{URL}")
            state["error"] = True
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
        sys.exit(1)

    new = [d for d in dates if d not in state.get("open", [])]
    now = dt.datetime.now(JST).strftime("%m/%d %H:%M")
    print(f"{now} checked months={months} 空有り={dates or 'なし'}")
    if new:
        notify("🍣 すし宮川に空きが出ました",
               "空有り: " + ", ".join(new) + f"\n電話: 011-613-2221\n{URL}\n({now} 検出)")
    if state.get("error"):
        notify("宮川監視: 復旧", f"チェックが再び成功しました ({now})")
    STATE_FILE.write_text(json.dumps({"open": dates, "error": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
