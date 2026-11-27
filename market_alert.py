import os
import json
import time
import math
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ==========================================
# BASED INTELLIGENCE V4.0
# SMART MARKET ALERT
# ==========================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COIN_ID = "based-one"
STATE = Path("market_alert_state.json")
KST = timezone(timedelta(hours=9))

# Alert configuration
PRICE_15M = 3.0
PRICE_1H = 5.0
PRICE_24H = 10.0
VOLUME_CHANGE = 200.0
COOLDOWN = 60 * 60

# Keep 72 hours of observations
HISTORY_LIMIT = 72 * 60 * 60

API_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&ids=based-one"
    "&price_change_percentage=1h,24h"
)

def request_json(url, payload=None):
    body = None

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BASED-Intelligence/4.0"
        }
    )

    with urllib.request.urlopen(
        request, timeout=30
    ) as response:
        return json.loads(response.read())

def telegram(message):
    try:
        result = request_json(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "link_preview_options": {
                    "is_disabled": True
                }
            }
        )

        if not result.get("ok"):
            raise RuntimeError()

    except Exception:
        raise RuntimeError(
            "Telegram delivery failed"
        ) from None

def load_state():
    if not STATE.exists():
        return {
            "history": [],
            "last_alerts": {},
            "initialized": False
        }

    return json.loads(
        STATE.read_text(encoding="utf-8")
    )

def save_state(state):
    temporary = STATE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8"
    )

    temporary.replace(STATE)

def numeric(value):
    if value is None:
        return None

    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except (ValueError, TypeError):
        pass

    return None

def get_market():
    data = request_json(API_URL)

    if not data or data[0].get("id") != COIN_ID:
        raise RuntimeError("Invalid BASED market data")

    coin = data[0]

    price = numeric(coin.get("current_price"))

    if price is None or price <= 0:
        raise RuntimeError("Invalid market price")

    updated = coin.get("last_updated")

    if not updated:
        raise RuntimeError("Missing market timestamp")

    timestamp = datetime.fromisoformat(
        updated.replace("Z", "+00:00")
    ).timestamp()

    if time.time() - timestamp > 1200:
        raise RuntimeError("Market data is stale")

    return {
        "timestamp": timestamp,
        "price": price,
        "volume": numeric(coin.get("total_volume")),
        "market_cap": numeric(coin.get("market_cap")),
        "fdv": numeric(coin.get("fully_diluted_valuation")),
        "change_1h": numeric(
            coin.get("price_change_percentage_1h_in_currency")
        ),
        "change_24h": numeric(
            coin.get("price_change_percentage_24h")
        )
    }

def find_previous(history, now, seconds, tolerance):
    target = now - seconds

    candidates = [
        item for item in history
        if item["timestamp"] < now
        and abs(item["timestamp"] - target) <= tolerance
    ]

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda item: abs(item["timestamp"] - target)
    )

def change_percent(current, previous):
    if current is None or previous is None:
        return None

    if previous <= 0:
        return None

    return (current - previous) / previous * 100

def percent(value):
    if value is None:
        return "수집 대기"

    icon = (
        "🟢" if value > 0
        else "🔴" if value < 0
        else "⚪"
    )

    return f"{icon} {value:+.2f}%"

def money(value):
    if value is None:
        return "확인 불가"

    for unit, scale in [
        ("B", 1e9),
        ("M", 1e6),
        ("K", 1e3)
    ]:
        if abs(value) >= scale:
            return f"${value / scale:,.2f}{unit}"

    return f"${value:,.2f}"

def current_time():
    return datetime.now(KST).strftime(
        "%Y.%m.%d · %H:%M KST"
    )

def check_cooldown(state, key, now):
    previous = state["last_alerts"].get(key, 0)

    return now - previous >= COOLDOWN

def build_message(market, changes, signals):
    keys = [item[0] for item in signals]

    has_up = any(key.endswith("_up") for key in keys)
    has_down = any(key.endswith("_down") for key in keys)

    if has_up and has_down:
        headline = "⚠️ MIXED MARKET SIGNALS"
    elif has_down:
        headline = "🔴 PRICE DROP DETECTED"
    elif has_up:
        headline = "🟢 PRICE SURGE DETECTED"
    else:
        headline = "📊 VOLUME SURGE DETECTED"

    signal_text = "\n".join(
        "⚠️ " + item[1] for item in signals
    )

    price = market["price"]
    cap = money(market["market_cap"])
    fdv = money(market["fdv"])
    volume = money(market["volume"])

    p15 = percent(changes["15m"])
    p1h = percent(changes["1h"])
    p24h = percent(changes["24h"])
    v24h = percent(changes["volume"])

    return f"""<b>◆ BASED INTELLIGENCE</b>
<b>🚨 MARKET VOLATILITY ALERT</b>
<i>{current_time()}</i>
━━━━━━━━━━━━━━━━━━

<b>{headline}</b>

<b>BASED / USD</b>
<b>${price:,.6f}</b>

<b>01 ┃ PRICE PERFORMANCE</b>
<blockquote>15분 변동　{p15}
1시간 변동　{p1h}
24시간 변동　{p24h}</blockquote>

<b>02 ┃ MARKET OVERVIEW</b>
시가총액　<b>{cap}</b>
FDV　　　{fdv}
24H 거래량　<b>{volume}</b>
거래량 증가율　{v24h}

<b>03 ┃ TRIGGER SIGNALS</b>
<blockquote>{signal_text}</blockquote>

<i>거래량은 24시간 누적 거래량을
24시간 전 관측값과 비교합니다.
15분 변동은 저장된 관측값 기준입니다.</i>

━━━━━━━━━━━━━━━━━━
<a href="https://www.coingecko.com/en/coins/based-one">↗ CoinGecko Market Details</a>

<b>BASED INTELLIGENCE</b> · V4.0"""

def main():
    state = load_state()
    market = get_market()

    now = time.time()
    observed = market["timestamp"]
    history = state["history"]

    # Avoid processing the same observation twice.
    if history and observed <= history[-1]["timestamp"]:
        print("No new market observation.")
        return

    previous_15m = find_previous(
        history, observed, 900, 600
    )

    previous_24h = find_previous(
        history, observed, 86400, 1800
    )

    change_15m = None
    volume_change = None

    if previous_15m:
        change_15m = change_percent(
            market["price"],
            previous_15m["price"]
        )

    if previous_24h:
        volume_change = change_percent(
            market["volume"],
            previous_24h["volume"]
        )

    changes = {
        "15m": change_15m,
        "1h": market["change_1h"],
        "24h": market["change_24h"],
        "volume": volume_change
    }

    signals = []

    rules = [
        ("15m", "15분", PRICE_15M),
        ("1h", "1시간", PRICE_1H),
        ("24h", "24시간", PRICE_24H)
    ]

    for key, label, threshold in rules:
        value = changes[key]

        if value is None:
            continue

        if abs(value) >= threshold:
            direction = "up" if value > 0 else "down"
            alert_key = key + "_" + direction

            if check_cooldown(state, alert_key, now):
                action = "급등" if value > 0 else "급락"

                signals.append((
                    alert_key,
                    f"{label} {action} {value:+.2f}%"
                ))

    if (
        volume_change is not None
        and volume_change >= VOLUME_CHANGE
        and check_cooldown(state, "volume", now)
    ):
        signals.append((
            "volume",
            f"거래량 급증 {volume_change:+.2f}%"
        ))

    if not state.get("initialized"):
        telegram(f"""<b>◆ BASED INTELLIGENCE</b>
<b>✅ SMART MARKET ALERT ONLINE</b>
<i>{current_time()}</i>
━━━━━━━━━━━━━━━━━━

<b>01 ┃ ALERT CONFIGURATION</b>
15분 가격 변동　±3%
1시간 가격 변동　±5%
24시간 가격 변동　±10%
거래량 증가　+200%
동일 유형 제한　60분

<b>02 ┃ CURRENT MARKET</b>
현재 가격　<b>${market['price']:,.6f}</b>
24H 거래량　{money(market['volume'])}

<b>03 ┃ SYSTEM STATUS</b>
시장 감시 시스템 활성화
15분마다 자동 데이터 수집
거래량 비교 데이터 축적 시작

━━━━━━━━━━━━━━━━━━
<b>BASED INTELLIGENCE</b> · V4.0""")

        state["initialized"] = True

    if signals:
        message = build_message(
            market, changes, signals
        )

        telegram(message)

        for key, _ in signals:
            state["last_alerts"][key] = now

        print(f"Alert sent: {len(signals)} signals")

    else:
        print("No new alert conditions.")

    history.append({
        "timestamp": observed,
        "price": market["price"],
        "volume": market["volume"]
    })

    state["history"] = [
        item for item in history
        if observed - item["timestamp"] <= HISTORY_LIMIT
    ]

    save_state(state)

    print(f"Price: ${market['price']:.6f}")
    print(f"15m change: {change_15m}")
    print(f"1h change: {changes['1h']}")
    print(f"24h change: {changes['24h']}")
    print(f"Volume change: {volume_change}")
    print(f"History records: {len(state['history'])}")
    print("Smart Market Alert completed.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {type(error).__name__}")
        raise SystemExit("Market alert failed.") from None
