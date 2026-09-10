from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, session


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BLUETITS_DB", BASE_DIR / "bluetits.sqlite3"))

PRIVY_APP_ID = os.environ.get(
    "PRIVY_APP_ID",
    "cmtvduxb801d80bl8rg0vb7km",
)
PRIVY_CLIENT_ID = os.environ.get(
    "PRIVY_CLIENT_ID",
    "client-WY6d8c5MW5M2jgjRZAy7oiet5LjQsEvJNiUCRZmZ3XVao",
)

NETWORKS = {
    "arc_testnet": {
        "name": "Arc Testnet",
        "chain_id": 5_042_002,
        "caip2": "eip155:5042002",
        "rpc": "https://rpc.testnet.arc.io",
        "rpc_fallbacks": [
            "https://rpc.testnet.arc.io",
            "https://rpc.drpc.testnet.arc.io",
            "https://rpc.blockdaemon.testnet.arc.io",
        ],
        "explorer": "https://testnet.arcscan.app",
        "explorer_name": "Arcscan",
        "usdc": "0x3600000000000000000000000000000000000000",
        "native_currency": {"name": "USDC", "symbol": "USDC", "decimals": 18},
        "environment": "testnet",
        "faucet_url": "https://faucet.circle.com/",
    },
    "base_mainnet": {
        "name": "Base Mainnet",
        "chain_id": 8453,
        "caip2": "eip155:8453",
        "rpc": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
        "explorer_name": "BaseScan",
        "usdc": "0x833589fCD6EDB6E08f4c7C32D4f71b54bdA02913",
        "native_currency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "environment": "mainnet",
    },
    "ethereum_mainnet": {
        "name": "Ethereum Mainnet",
        "chain_id": 1,
        "caip2": "eip155:1",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "explorer": "https://etherscan.io",
        "explorer_name": "Etherscan",
        "usdc": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "native_currency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
        "environment": "mainnet",
    },
}
ARC_NETWORK = NETWORKS["arc_testnet"]
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
DATA_URL_RE = re.compile(
    r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$"
)

MAX_COMMISSION_IMAGE_BYTES = 500_000
MAX_DELIVERY_IMAGE_BYTES = 700_000

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get(
    "BLUETITS_SECRET_KEY",
    "dev-only-change-me-before-deployment",
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                wallet TEXT PRIMARY KEY,
                privy_user_id TEXT,
                email TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_wallet TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                min_price_micros INTEGER NOT NULL,
                max_price_micros INTEGER NOT NULL,
                image_mime TEXT NOT NULL,
                image_blob BLOB NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (artist_wallet) REFERENCES users(wallet)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_commissions_artist
                ON commissions(artist_wallet);

            CREATE TABLE IF NOT EXISTS commission_requests (
                id TEXT PRIMARY KEY,
                artist_wallet TEXT NOT NULL,
                buyer_wallet TEXT NOT NULL,
                commission_id INTEGER NOT NULL,
                commission_title TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,

                offer_amount_micros INTEGER,
                offer_recipient TEXT,
                offer_chain_id INTEGER,
                offer_sent_at TEXT,

                tx_hash TEXT UNIQUE,
                payment_from_wallet TEXT,
                payment_block_number TEXT,
                paid_at TEXT,

                delivery_mime TEXT,
                delivery_blob BLOB,
                delivery_message TEXT,
                delivered_at TEXT,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (artist_wallet) REFERENCES users(wallet),
                FOREIGN KEY (buyer_wallet) REFERENCES users(wallet),
                FOREIGN KEY (commission_id) REFERENCES commissions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_requests_artist
                ON commission_requests(artist_wallet);
            CREATE INDEX IF NOT EXISTS idx_requests_buyer
                ON commission_requests(buyer_wallet);
            """
        )

        # Existing prototype databases were created before email was stored.
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        }
        if "email" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")


def normalize_wallet(value: str | None) -> str:
    value = (value or "").strip()
    if not WALLET_RE.match(value):
        raise ValueError("Invalid EVM wallet address.")
    return value.lower()


def current_wallet(required: bool = True) -> str | None:
    wallet = session.get("wallet")
    if wallet:
        return wallet
    if required:
        raise PermissionError("Authentication required.")
    return None


def parse_money_to_micros(value) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid monetary amount.")

    if amount < 0:
        raise ValueError("Amount cannot be negative.")

    micros = (amount * Decimal(1_000_000)).quantize(
        Decimal("1"),
        rounding=ROUND_DOWN,
    )
    return int(micros)


def micros_to_string(value: int | None) -> str | None:
    if value is None:
        return None

    whole = value // 1_000_000
    fraction = value % 1_000_000

    if fraction == 0:
        return str(whole)

    fraction_text = f"{fraction:06d}".rstrip("0")
    return f"{whole}.{fraction_text}"


def decode_image_data_url(value: str, max_bytes: int) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise ValueError("Image must be a data URL.")

    match = DATA_URL_RE.match(value)
    if not match:
        raise ValueError("Only JPEG, PNG and WebP image data URLs are supported.")

    mime = match.group(1)
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except Exception as exc:
        raise ValueError("Invalid base64 image data.") from exc

    if not raw:
        raise ValueError("Image is empty.")

    if len(raw) > max_bytes:
        raise ValueError(
            f"Compressed image is still too large ({len(raw)} bytes). "
            f"Maximum is {max_bytes} bytes."
        )

    return mime, raw


def commission_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "artist_wallet": row["artist_wallet"],
        "title": row["title"],
        "description": row["description"],
        "min_price": micros_to_string(row["min_price_micros"]),
        "max_price": micros_to_string(row["max_price_micros"]),
        "image_url": f"/api/commissions/{row['id']}/image",
        "created_at": row["created_at"],
    }


def request_json(row: sqlite3.Row) -> dict:
    offer = None
    if row["offer_amount_micros"] is not None:
        offer = {
            "amount": micros_to_string(row["offer_amount_micros"]),
            "token": "USDC",
            "network": ARC_NETWORK["name"],
            "chain_id": row["offer_chain_id"],
            "recipient": row["offer_recipient"],
            "sent_at": row["offer_sent_at"],
        }

    payment = None
    if row["tx_hash"]:
        payment = {
            "real": True,
            "tx_hash": row["tx_hash"],
            "from_wallet": row["payment_from_wallet"],
            "recipient": row["offer_recipient"],
            "block_number": row["payment_block_number"],
            "paid_at": row["paid_at"],
        }

    delivery = None
    if row["delivery_blob"] is not None:
        delivery = {
            "image_url": f"/api/requests/{row['id']}/delivery-image",
            "message": row["delivery_message"] or "",
            "delivered_at": row["delivered_at"],
        }

    return {
        "id": row["id"],
        "artist_wallet": row["artist_wallet"],
        "buyer_wallet": row["buyer_wallet"],
        "commission_id": row["commission_id"],
        "commission_title": row["commission_title"],
        "message": row["message"],
        "status": row["status"],
        "offer": offer,
        "payment": payment,
        "delivery": delivery,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def rpc(method: str, params: list) -> object:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
    ).encode("utf-8")

    errors = []

    for endpoint in ARC_NETWORK["rpc_fallbacks"]:
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "BlueTits/0.1",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                body = json.loads(response.read().decode("utf-8"))

            if body.get("error"):
                raise RuntimeError(
                    body["error"].get("message", "Arc RPC error.")
                )

            return body.get("result")
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")

    raise RuntimeError(
        "All Arc RPC endpoints failed: " + " | ".join(errors)
    )


def topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def verify_payment_receipt(
    tx_hash: str,
    expected_from: str,
    expected_to: str,
    expected_amount: int,
) -> tuple[bool, dict | None]:
    receipt = rpc("eth_getTransactionReceipt", [tx_hash])

    if not receipt:
        return False, None

    if receipt.get("status") != "0x1":
        return False, receipt

    expected_from = expected_from.lower()
    expected_to = expected_to.lower()

    for log in receipt.get("logs", []):
        if (log.get("address") or "").lower() != ARC_NETWORK["usdc"].lower():
            continue

        topics = log.get("topics") or []
        if len(topics) < 3:
            continue

        if topics[0].lower() != TRANSFER_TOPIC.lower():
            continue

        actual_from = topic_address(topics[1])
        actual_to = topic_address(topics[2])
        actual_amount = int(log.get("data") or "0x0", 16)

        if (
            actual_from == expected_from
            and actual_to == expected_to
            and actual_amount == expected_amount
        ):
            return True, receipt

    return False, receipt


@app.errorhandler(ValueError)
def handle_value_error(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(PermissionError)
def handle_permission_error(error):
    return jsonify({"error": str(error)}), 401


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/config")
def config():
    return jsonify(
        {
            "privy_app_id": PRIVY_APP_ID,
            "privy_client_id": PRIVY_CLIENT_ID,
            "networks": NETWORKS,
            "commission_payment_network": "arc_testnet",
            "fiat_onramp_environment": "sandbox",
        }
    )


# IMPORTANT:
# This demo endpoint trusts the wallet address that the browser reports after a
# successful Privy login. That is convenient for a prototype, but it is NOT
# sufficient authentication for production. The next hardening step is to send
# a Privy access/identity token to this endpoint and verify it server-side before
# accepting the wallet/user identity.
@app.post("/api/session")
def create_session():
    data = json_body()
    wallet = normalize_wallet(data.get("wallet"))
    privy_user_id = str(data.get("privy_user_id") or "")[:300]
    email = str(data.get("email") or "").strip()[:320]
    now = utcnow()

    with db() as conn:
        existing = conn.execute(
            "SELECT wallet FROM users WHERE wallet = ?",
            (wallet,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE users
                SET privy_user_id = ?, email = ?, last_login_at = ?
                WHERE wallet = ?
                """,
                (privy_user_id, email, now, wallet),
            )
        else:
            conn.execute(
                """
                INSERT INTO users(wallet, privy_user_id, email, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (wallet, privy_user_id, email, now, now),
            )

    session["wallet"] = wallet

    return jsonify(
        {
            "user": {
                "wallet": wallet,
                "privy_user_id": privy_user_id,
                "email": email,
            }
        }
    )


@app.delete("/api/session")
def delete_session():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    wallet = current_wallet(required=False)

    if not wallet:
        return jsonify({"user": None})

    with db() as conn:
        row = conn.execute(
            """
            SELECT wallet, privy_user_id, email, created_at, last_login_at
            FROM users
            WHERE wallet = ?
            """,
            (wallet,),
        ).fetchone()

    if not row:
        session.clear()
        return jsonify({"user": None})

    return jsonify({"user": dict(row)})


@app.get("/api/users")
def users():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                u.wallet,
                u.privy_user_id,
                u.email,
                u.created_at,
                u.last_login_at,
                COUNT(c.id) AS commission_count,
                MIN(c.id) AS first_commission_id
            FROM users u
            LEFT JOIN commissions c
                ON c.artist_wallet = u.wallet
            GROUP BY u.wallet
            ORDER BY u.last_login_at DESC, u.created_at DESC
            """
        ).fetchall()

    result = []
    for row in rows:
        cover_url = None
        if row["first_commission_id"] is not None:
            cover_url = f"/api/commissions/{row['first_commission_id']}/image"

        result.append(
            {
                "wallet": row["wallet"],
                "email": row["email"],
                "commission_count": row["commission_count"],
                "cover_url": cover_url,
                "created_at": row["created_at"],
                "last_login_at": row["last_login_at"],
            }
        )

    return jsonify({"users": result})


@app.get("/api/users/<wallet>")
def user_profile(wallet):
    wallet = normalize_wallet(wallet)

    with db() as conn:
        user = conn.execute(
            """
            SELECT wallet, privy_user_id, email, created_at, last_login_at
            FROM users
            WHERE wallet = ?
            """,
            (wallet,),
        ).fetchone()

        if not user:
            return jsonify({"error": "User not found."}), 404

        rows = conn.execute(
            """
            SELECT *
            FROM commissions
            WHERE artist_wallet = ?
            ORDER BY id DESC
            """,
            (wallet,),
        ).fetchall()

    return jsonify(
        {
            "user": {
                "wallet": user["wallet"],
                "email": user["email"],
                "created_at": user["created_at"],
                "last_login_at": user["last_login_at"],
            },
            "commissions": [commission_json(row) for row in rows],
        }
    )


@app.post("/api/commissions")
def create_commission():
    actor = current_wallet()
    data = json_body()

    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()

    if not title:
        raise ValueError("Commission title is required.")

    min_price = parse_money_to_micros(data.get("min_price"))
    max_price = parse_money_to_micros(data.get("max_price"))

    if max_price < min_price:
        raise ValueError("Maximum price must be at least the minimum price.")

    mime, image = decode_image_data_url(
        data.get("image"),
        MAX_COMMISSION_IMAGE_BYTES,
    )

    now = utcnow()

    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO commissions(
                artist_wallet,
                title,
                description,
                min_price_micros,
                max_price_micros,
                image_mime,
                image_blob,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                title[:200],
                description[:5000],
                min_price,
                max_price,
                mime,
                image,
                now,
            ),
        )

        row = conn.execute(
            "SELECT * FROM commissions WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    return jsonify({"commission": commission_json(row)}), 201


@app.delete("/api/commissions/<int:commission_id>")
def delete_commission(commission_id):
    actor = current_wallet()

    with db() as conn:
        row = conn.execute(
            "SELECT artist_wallet FROM commissions WHERE id = ?",
            (commission_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Commission not found."}), 404

        if row["artist_wallet"] != actor:
            return jsonify({"error": "Forbidden."}), 403

        request_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM commission_requests
            WHERE commission_id = ?
            """,
            (commission_id,),
        ).fetchone()["count"]

        if request_count:
            return jsonify(
                {
                    "error": (
                        "Cannot delete a commission after it has been used in an order."
                    )
                }
            ), 409

        conn.execute(
            "DELETE FROM commissions WHERE id = ?",
            (commission_id,),
        )

    return jsonify({"ok": True})


@app.get("/api/commissions/<int:commission_id>/image")
def commission_image(commission_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT image_mime, image_blob
            FROM commissions
            WHERE id = ?
            """,
            (commission_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "Image not found."}), 404

    return Response(
        row["image_blob"],
        mimetype=row["image_mime"],
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/requests")
def create_request():
    buyer = current_wallet()
    data = json_body()

    try:
        commission_id = int(data.get("commission_id"))
    except (TypeError, ValueError):
        raise ValueError("Invalid commission id.")

    message = str(data.get("message") or "").strip()
    if not message:
        raise ValueError("Request message is required.")

    with db() as conn:
        commission = conn.execute(
            """
            SELECT id, artist_wallet, title
            FROM commissions
            WHERE id = ?
            """,
            (commission_id,),
        ).fetchone()

        if not commission:
            return jsonify({"error": "Commission not found."}), 404

        artist = commission["artist_wallet"]

        if artist == buyer:
            raise ValueError("You cannot request your own commission.")

        duplicate = conn.execute(
            """
            SELECT id
            FROM commission_requests
            WHERE commission_id = ?
              AND buyer_wallet = ?
              AND status IN ('requested', 'offered', 'paid')
            LIMIT 1
            """,
            (commission_id, buyer),
        ).fetchone()

        if duplicate:
            return jsonify(
                {
                    "error": (
                        "You already have an active request for this commission."
                    )
                }
            ), 409

        request_id = str(uuid.uuid4())
        now = utcnow()

        conn.execute(
            """
            INSERT INTO commission_requests(
                id,
                artist_wallet,
                buyer_wallet,
                commission_id,
                commission_title,
                message,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'requested', ?, ?)
            """,
            (
                request_id,
                artist,
                buyer,
                commission_id,
                commission["title"],
                message[:10000],
                now,
                now,
            ),
        )

        row = conn.execute(
            "SELECT * FROM commission_requests WHERE id = ?",
            (request_id,),
        ).fetchone()

    return jsonify({"request": request_json(row)}), 201


@app.get("/api/requests")
def list_requests():
    actor = current_wallet()

    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM commission_requests
            WHERE artist_wallet = ? OR buyer_wallet = ?
            ORDER BY created_at DESC
            """,
            (actor, actor),
        ).fetchall()

    return jsonify({"requests": [request_json(row) for row in rows]})


def get_request_for_actor(
    conn: sqlite3.Connection,
    request_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM commission_requests
        WHERE id = ?
        """,
        (request_id,),
    ).fetchone()


@app.post("/api/requests/<request_id>/decline")
def decline_request(request_id):
    actor = current_wallet()

    with db() as conn:
        row = get_request_for_actor(conn, request_id)

        if not row:
            return jsonify({"error": "Request not found."}), 404

        if row["artist_wallet"] != actor:
            return jsonify({"error": "Forbidden."}), 403

        if row["status"] != "requested":
            return jsonify({"error": "Only requested orders can be declined."}), 409

        now = utcnow()
        conn.execute(
            """
            UPDATE commission_requests
            SET status = 'declined', updated_at = ?
            WHERE id = ?
            """,
            (now, request_id),
        )

        updated = get_request_for_actor(conn, request_id)

    return jsonify({"request": request_json(updated)})


@app.post("/api/requests/<request_id>/offer")
def offer_request(request_id):
    actor = current_wallet()
    data = json_body()
    amount = parse_money_to_micros(data.get("amount"))

    if amount <= 0:
        raise ValueError("Offer amount must be greater than zero.")

    with db() as conn:
        row = get_request_for_actor(conn, request_id)

        if not row:
            return jsonify({"error": "Request not found."}), 404

        if row["artist_wallet"] != actor:
            return jsonify({"error": "Forbidden."}), 403

        if row["status"] != "requested":
            return jsonify({"error": "Only requested orders can receive an offer."}), 409

        now = utcnow()
        conn.execute(
            """
            UPDATE commission_requests
            SET
                status = 'offered',
                offer_amount_micros = ?,
                offer_recipient = ?,
                offer_chain_id = ?,
                offer_sent_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                amount,
                actor,
                ARC_NETWORK["chain_id"],
                now,
                now,
                request_id,
            ),
        )

        updated = get_request_for_actor(conn, request_id)

    return jsonify({"request": request_json(updated)})


@app.post("/api/requests/<request_id>/confirm-payment")
def confirm_payment(request_id):
    actor = current_wallet()
    data = json_body()
    tx_hash = str(data.get("tx_hash") or "").strip()

    if not re.match(r"^0x[a-fA-F0-9]{64}$", tx_hash):
        raise ValueError("Invalid transaction hash.")

    with db() as conn:
        row = get_request_for_actor(conn, request_id)

        if not row:
            return jsonify({"error": "Request not found."}), 404

        if row["buyer_wallet"] != actor:
            return jsonify({"error": "Forbidden."}), 403

        if row["status"] != "offered":
            return jsonify({"error": "This order is not waiting for payment."}), 409

        if row["offer_chain_id"] != ARC_NETWORK["chain_id"]:
            return jsonify({"error": "Offer is for an unsupported chain."}), 409

        amount = row["offer_amount_micros"]
        recipient = row["offer_recipient"]

    try:
        valid, receipt = verify_payment_receipt(
            tx_hash,
            actor,
            recipient,
            amount,
        )
    except Exception as exc:
        app.logger.exception("Arc RPC verification failed")
        return jsonify({"error": f"Arc RPC verification failed: {exc}"}), 502

    if not receipt:
        return jsonify(
            {
                "error": (
                    "Transaction has not been confirmed yet. "
                    "Try again in a moment."
                )
            }
        ), 409

    if not valid:
        return jsonify(
            {
                "error": (
                    "Transaction exists, but it does not contain the expected "
                    "USDC transfer from the buyer to the artist."
                )
            }
        ), 400

    block_number = receipt.get("blockNumber")
    now = utcnow()

    try:
        with db() as conn:
            conn.execute(
                """
                UPDATE commission_requests
                SET
                    status = 'paid',
                    tx_hash = ?,
                    payment_from_wallet = ?,
                    payment_block_number = ?,
                    paid_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    tx_hash.lower(),
                    actor,
                    block_number,
                    now,
                    now,
                    request_id,
                ),
            )

            updated = get_request_for_actor(conn, request_id)
    except sqlite3.IntegrityError:
        return jsonify(
            {"error": "This transaction hash has already been used."}
        ), 409

    return jsonify({"request": request_json(updated)})


@app.post("/api/requests/<request_id>/delivery")
def deliver_request(request_id):
    actor = current_wallet()
    data = json_body()
    message = str(data.get("message") or "").strip()

    mime, image = decode_image_data_url(
        data.get("image"),
        MAX_DELIVERY_IMAGE_BYTES,
    )

    with db() as conn:
        row = get_request_for_actor(conn, request_id)

        if not row:
            return jsonify({"error": "Request not found."}), 404

        if row["artist_wallet"] != actor:
            return jsonify({"error": "Forbidden."}), 403

        if row["status"] not in ("paid", "delivered"):
            return jsonify(
                {"error": "Artwork can only be delivered after payment."}
            ), 409

        now = utcnow()
        conn.execute(
            """
            UPDATE commission_requests
            SET
                status = 'delivered',
                delivery_mime = ?,
                delivery_blob = ?,
                delivery_message = ?,
                delivered_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                mime,
                image,
                message[:5000],
                now,
                now,
                request_id,
            ),
        )

        updated = get_request_for_actor(conn, request_id)

    return jsonify({"request": request_json(updated)})


@app.get("/api/requests/<request_id>/delivery-image")
def delivery_image(request_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT delivery_mime, delivery_blob
            FROM commission_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()

    if not row or row["delivery_blob"] is None:
        return jsonify({"error": "Delivery image not found."}), 404

    return Response(
        row["delivery_blob"],
        mimetype=row["delivery_mime"],
        headers={"Cache-Control": "private, max-age=300"},
    )


init_db()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
