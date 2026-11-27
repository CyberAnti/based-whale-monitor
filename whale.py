import os
import json
import time
import urllib.request
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
THRESHOLD = Decimal("100000")
STATE = Path("whale_state.json")
KST = timezone(timedelta(hours=9))

TRANSFER = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

NETWORKS = {
    "Ethereum": {
        "id": 1,
        "rpc": [
            "https://ethereum-rpc.publicnode.com",
            "https://eth.drpc.org"
        ],
        "contract": "0x4f2b33840227ddd0e28da8d4185d6fa07adfed87",
        "explorer": "https://etherscan.io/tx/",
        "confirmations": 64,
        "chunk": 500
    },
    "BNB Chain": {
        "id": 56,
        "rpc": [
            "https://bsc-rpc.publicnode.com",
            "https://bsc.drpc.org"
        ],
        "contract": "0x1d28d989f9e3ccb8b15d0cec601734514f958e4d",
        "explorer": "https://bscscan.com/tx/",
        "confirmations": 30,
        "chunk": 500
    },
    "HyperEVM": {
        "id": 999,
        "rpc": [
            "https://rpc.hyperliquid.xyz/evm",
            "https://hyperevm-rpc.publicnode.com"
        ],
        "contract": "0x053f6755320d06b8fd6675581b0475b2e32399b1",
        "explorer": "https://hyperevmscan.io/tx/",
        "confirmations": 20,
        "chunk": 500
    }
}

def request_json(url, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BASED-Whale-Monitor/1.0"
        }
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        return json.loads(response.read())

def rpc(config, method, params):
    for endpoint in config["rpc"]:
        try:
            result = request_json(endpoint, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            })

            if "error" in result:
                continue

            if result.get("result") is not None:
                return result["result"]

        except Exception:
            continue

    raise RuntimeError(f"RPC failed: {method}")

def telegram(message):
    try:
        result = request_json(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            {
                "chat_id": CHAT_ID,
                "text": message,
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

def get_price():
    try:
        data = request_json(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=based-one&vs_currencies=usd"
        )
        return Decimal(str(data["based-one"]["usd"]))
    except Exception:
        return None

def load_state():
    if not STATE.exists():
        return {}

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

def whale_level(amount):
    if amount >= 5000000:
        return "🚨 초대형 자금 이동"
    if amount >= 1000000:
        return "🚨 초대형 고래"
    if amount >= 500000:
        return "🐋🐋 대형 고래"
    return "🐋 일반 고래"

def address(topic):
    return "0x" + topic[-40:].lower()

def transfer_type(sender, receiver):
    zero = "0x" + "0" * 40
    dead = "0x" + "0" * 36 + "dead"

    if sender == zero:
        return "발행 이벤트 (브리지 포함 가능)"
    if receiver == zero:
        return "소각 이벤트 (브리지 포함 가능)"
    if receiver == dead:
        return "소각 주소로 전송"
    return "일반 토큰 전송"

def get_logs(config, start, end):
    try:
        return rpc(config, "eth_getLogs", [{
            "address": config["contract"],
            "fromBlock": hex(start),
            "toBlock": hex(end),
            "topics": [TRANSFER]
        }])

    except RuntimeError:
        if start == end:
            raise

        middle = (start + end) // 2

        return (
            get_logs(config, start, middle)
            + get_logs(config, middle + 1, end)
        )

def scan_network(name, config, state, price):
    chain_id = int(
        rpc(config, "eth_chainId", []), 16
    )

    if chain_id != config["id"]:
        raise RuntimeError("Incorrect chain ID")

    decimals = int(rpc(config, "eth_call", [{
        "to": config["contract"],
        "data": "0x313ce567"
    }, "latest"]), 16)

    if not 0 <= decimals <= 36:
        raise RuntimeError("Invalid token decimals")

    latest = int(
        rpc(config, "eth_blockNumber", []), 16
    )
    target = latest - config["confirmations"]

    if name not in state:
        telegram(
            f"✅ BASED 고래 추적 초기화 완료\n"
            f"네트워크: {name}\n"
            f"감지 기준: 100,000 BASED\n"
            f"기준 블록: {target}\n"
            f"이후 신규 거래부터 추적합니다."
        )

        state[name] = {
            "last_block": target,
            "sent": []
        }
        save_state(state)

        print(f"{name}: initialized")
        return

    record = state[name]
    start = record["last_block"] + 1
    sent = set(record.get("sent", []))

    for _ in range(20):
        if start > target:
            break

        end = min(
            start + config["chunk"] - 1,
            target
        )

        logs = get_logs(config, start, end)

        logs.sort(key=lambda log: (
            int(log["blockNumber"], 16),
            int(log["logIndex"], 16)
        ))

        for log in logs:
            if log.get("removed"):
                continue

            topics = log.get("topics", [])

            if len(topics) != 3:
                continue

            raw = int(log["data"], 16)
            amount = (
                Decimal(raw)
                / Decimal(10 ** decimals)
            )

            if amount < THRESHOLD:
                continue

            tx = log["transactionHash"]

            event_id = (
                tx + ":" + str(
                    int(log["logIndex"], 16)
                )
            )

            if event_id in sent:
                continue

            sender = address(topics[1])
            receiver = address(topics[2])

            usd = "확인 불가"

            if price is not None:
                usd = f"${amount * price:,.2f}"

            supply_ratio = (
                amount / Decimal("1000000000")
                * 100
            )

            now = datetime.now(KST).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            message = f"""🚨 BASED WHALE ALERT
━━━━━━━━━━━━━━━━━━
{whale_level(amount)}

네트워크: {name}
감지 시간: {now} KST

💰 이동 수량
{amount:,.2f} BASED

현재 시세 환산: {usd}
최대 공급량 대비: {supply_ratio:.4f}%

📤 출발 지갑
{sender}

📥 도착 지갑
{receiver}

거래 유형:
{transfer_type(sender, receiver)}

🔗 트랜잭션
{config["explorer"]}{tx}

※ 단순 전송은 매수·매도를
의미하지 않습니다.
━━━━━━━━━━━━━━━━━━
🤖 BASED Whale Monitor"""

            telegram(message)

            sent.add(event_id)
            record.setdefault("sent", []).append(
                event_id
            )
            save_state(state)
            time.sleep(1)

        record["last_block"] = end
        record["sent"] = []
        sent.clear()
        save_state(state)

        start = end + 1

    print(
        f"{name}: scanned through block "
        f"{record['last_block']}, target {target}"
    )

    if record["last_block"] < target:
        print(f"{name}: backlog remains")

def main():
    state = load_state()
    price = get_price()
    failed = []

    for name, config in NETWORKS.items():
        try:
            scan_network(
                name, config, state, price
            )
        except Exception as error:
            print(
                f"{name}: {type(error).__name__}"
            )
            failed.append(name)

    save_state(state)

    if failed:
        raise SystemExit(
            "Scan incomplete: " + ", ".join(failed)
        )

    print("Whale scan completed.")

if __name__ == "__main__":
    main()
