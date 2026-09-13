from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

import jwt
from flask import Flask, Response, jsonify, request
from jwt import InvalidKeyError, InvalidTokenError


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DB_PATH = Path(os.environ.get("BLUETITS_DB", PROJECT_DIR / "bluetits.sqlite3"))
FRONTEND_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("BLUETITS_FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
}

PRIVY_APP_ID = os.environ.get(
    "PRIVY_APP_ID",
    "cmtvduxb801d80bl8rg0vb7km",
)
PRIVY_CLIENT_ID = os.environ.get(
    "PRIVY_CLIENT_ID",
    "client-WY6d8c5MW5M2jgjRZAy7oiet5LjQsEvJNiUCRZmZ3XVao",
)
PRIVY_VERIFICATION_KEY = os.environ.get(
    "PRIVY_VERIFICATION_KEY",
    "",
).replace("\\n", "\n").strip()

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
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,22}[a-z0-9])?$")
DATA_URL_RE = re.compile(
    r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$"
)

MAX_COMMISSION_IMAGE_BYTES = 500_000
MAX_DELIVERY_IMAGE_BYTES = 700_000
MAX_AVATAR_IMAGE_BYTES = 500_000
RESERVED_USERNAMES = {
    "admin", "api", "app", "assets", "login", "logout", "settings",
    "static", "users", "profile", "profiles",
}

app = Flask(__name__, static_folder=None)


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in FRONTEND_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, DELETE, OPTIONS"
        )
        response.headers.add("Vary", "Origin")
    return response


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
                username TEXT,
                avatar_mime TEXT,
                avatar_blob BLOB,
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
        if "username" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "avatar_mime" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT")
        if "avatar_blob" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN avatar_blob BLOB")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase
            ON users(username COLLATE NOCASE)
            WHERE username IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_privy_user_id
            ON users(privy_user_id)
            WHERE privy_user_id IS NOT NULL AND privy_user_id != ''
            """
        )


def delete_user_by_email(email: str) -> dict:
    """Delete one user and all marketplace data owned by or addressed to them."""
    email = (email or "").strip()
    if not email:
        raise ValueError("User email is required.")

    with db() as conn:
        users = conn.execute(
            """
            SELECT wallet, email
            FROM users
            WHERE email IS NOT NULL AND lower(email) = lower(?)
            """,
            (email,),
        ).fetchall()

        if not users:
            raise LookupError(f"User not found: {email}")
        if len(users) > 1:
            raise RuntimeError(
                f"More than one user matches this email: {email}"
            )

        wallet = users[0]["wallet"]

        # Requests must be removed before commissions/users because they
        # reference both tables without ON DELETE CASCADE.
        requests_deleted = conn.execute(
            """
            DELETE FROM commission_requests
            WHERE artist_wallet = ? OR buyer_wallet = ?
            """,
            (wallet, wallet),
        ).rowcount
        commissions_deleted = conn.execute(
            "DELETE FROM commissions WHERE artist_wallet = ?",
            (wallet,),
        ).rowcount
        users_deleted = conn.execute(
            "DELETE FROM users WHERE wallet = ?",
            (wallet,),
        ).rowcount

    return {
        "email": users[0]["email"] or email,
        "wallet": wallet,
        "requests_deleted": requests_deleted,
        "commissions_deleted": commissions_deleted,
        "users_deleted": users_deleted,
    }


def cli() -> int:
    parser = argparse.ArgumentParser(description="BlueTits server utilities")
    subparsers = parser.add_subparsers(dest="command")

    deleteuser_parser = subparsers.add_parser(
        "deleteuser",
        help="delete a user by email and their related marketplace data",
    )
    deleteuser_parser.add_argument("email", help="user email")

    args = parser.parse_args()
    if args.command == "deleteuser":
        try:
            result = delete_user_by_email(args.email)
        except (LookupError, RuntimeError, ValueError) as error:
            parser.error(str(error))

        print(
            "Deleted user {email} ({wallet}); removed {commissions_deleted} "
            "commission(s) and {requests_deleted} request(s).".format(**result)
        )
        return 0

    parser.print_help()
    return 0


def normalize_wallet(value: str | None) -> str:
    value = (value or "").strip()
    if not WALLET_RE.match(value):
        raise ValueError("Invalid EVM wallet address.")
    return value.lower()


def normalize_username(value: str | None) -> str:
    username = (value or "").strip().lower()
    if username.startswith("@"):
        username = username[1:]
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Username must be 3-24 characters: lowercase letters, numbers, "
            "underscore or hyphen."
        )
    if username in RESERVED_USERNAMES:
        raise ValueError("This username is reserved.")
    return username


def avatar_url(wallet: str) -> str:
    return f"/api/users/{wallet}/avatar"


def public_user_json(row: sqlite3.Row) -> dict:
    return {
        "wallet": row["wallet"],
        "username": row["username"],
        "avatar_url": avatar_url(row["wallet"])
        if row["avatar_blob"] is not None
        else None,
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


class ServerConfigurationError(RuntimeError):
    pass


def current_privy_user_id(required: bool = True) -> str | None:
    authorization = request.headers.get("Authorization", "").strip()
    if not authorization:
        if required:
            raise PermissionError("Authentication required.")
        return None

    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise PermissionError("Invalid Authorization header.")

    if not PRIVY_VERIFICATION_KEY:
        raise ServerConfigurationError(
            "PRIVY_VERIFICATION_KEY is not configured on the server."
        )

    try:
        claims = jwt.decode(
            token.strip(),
            PRIVY_VERIFICATION_KEY,
            algorithms=["ES256"],
            audience=PRIVY_APP_ID,
            issuer="privy.io",
            options={"require": ["aud", "exp", "iat", "iss", "sub"]},
        )
    except (InvalidKeyError, ValueError) as error:
        raise ServerConfigurationError(
            "PRIVY_VERIFICATION_KEY is not a valid ES256 public key."
        ) from error
    except InvalidTokenError as error:
        app.logger.info("Rejected Privy access token: %s", error)
        raise PermissionError("Invalid or expired Privy access token.") from error

    privy_user_id = claims.get("sub")
    if not isinstance(privy_user_id, str) or not privy_user_id.startswith(
        "did:privy:"
    ):
        raise PermissionError("Invalid Privy access token subject.")

    return privy_user_id


def current_wallet(required: bool = True) -> str | None:
    privy_user_id = current_privy_user_id(required=required)
    if not privy_user_id:
        return None

    with db() as conn:
        row = conn.execute(
            "SELECT wallet FROM users WHERE privy_user_id = ? LIMIT 1",
            (privy_user_id,),
        ).fetchone()

    if row:
        return row["wallet"]
    if required:
        raise PermissionError("Privy account is not registered.")
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


def network_by_key(key: str | None) -> tuple[str, dict]:
    key = str(key or "").strip()
    network = NETWORKS.get(key)
    if not network:
        raise ValueError("Unsupported payment network.")
    return key, network


def network_by_chain_id(chain_id: int | None) -> tuple[str, dict] | None:
    for key, network in NETWORKS.items():
        if network["chain_id"] == chain_id:
            return key, network
    return None


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
        network_match = network_by_chain_id(row["offer_chain_id"])
        network_key, network = network_match or (
            "unsupported",
            {"name": "Unsupported network", "environment": "unknown"},
        )
        offer = {
            "amount": micros_to_string(row["offer_amount_micros"]),
            "token": "USDC",
            "network": network["name"],
            "network_key": network_key,
            "environment": network["environment"],
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


def rpc(network: dict, method: str, params: list) -> object:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
    ).encode("utf-8")

    errors = []

    endpoints = network.get("rpc_fallbacks") or [network["rpc"]]
    for endpoint in endpoints:
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
                    body["error"].get("message", "RPC error.")
                )

            return body.get("result")
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")

    raise RuntimeError(
        f"All {network['name']} RPC endpoints failed: " + " | ".join(errors)
    )


def topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def verify_payment_receipt(
    network: dict,
    tx_hash: str,
    expected_from: str,
    expected_to: str,
    expected_amount: int,
) -> tuple[bool, dict | None]:
    receipt = rpc(network, "eth_getTransactionReceipt", [tx_hash])

    if not receipt:
        return False, None

    if receipt.get("status") != "0x1":
        return False, receipt

    expected_from = expected_from.lower()
    expected_to = expected_to.lower()

    for log in receipt.get("logs", []):
        if (log.get("address") or "").lower() != network["usdc"].lower():
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


@app.errorhandler(ServerConfigurationError)
def handle_configuration_error(error):
    app.logger.error("Server authentication configuration error: %s", error)
    return jsonify({"error": str(error)}), 503


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
            "fiat_onramp_environment": "sandbox",
        }
    )


@app.post("/api/session")
def create_session():
    privy_user_id = current_privy_user_id()
    data = json_body()
    wallet = normalize_wallet(data.get("wallet"))
    email = str(data.get("email") or "").strip()[:320]
    now = utcnow()

    with db() as conn:
        existing_identity = conn.execute(
            "SELECT wallet FROM users WHERE privy_user_id = ? LIMIT 1",
            (privy_user_id,),
        ).fetchone()
        existing_wallet = conn.execute(
            "SELECT privy_user_id FROM users WHERE wallet = ?",
            (wallet,),
        ).fetchone()

        if existing_identity and existing_identity["wallet"] != wallet:
            return jsonify(
                {"error": "This Privy account is linked to another wallet."}
            ), 409

        if (
            existing_wallet
            and existing_wallet["privy_user_id"]
            and existing_wallet["privy_user_id"] != privy_user_id
        ):
            return jsonify(
                {"error": "This wallet is linked to another Privy account."}
            ), 409

        if existing_wallet:
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

    with db() as conn:
        row = conn.execute(
            """
            SELECT wallet, username, avatar_mime, avatar_blob,
                   created_at, last_login_at
            FROM users WHERE wallet = ?
            """,
            (wallet,),
        ).fetchone()

    return jsonify({"user": public_user_json(row)})


@app.delete("/api/session")
def delete_session():
    # Access tokens are stateless. Privy invalidates its browser-side session;
    # this endpoint only verifies that the logout request was authenticated.
    current_privy_user_id()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    wallet = current_wallet(required=False)

    if not wallet:
        return jsonify({"user": None})

    with db() as conn:
        row = conn.execute(
            """
            SELECT wallet, username, avatar_mime, avatar_blob,
                   created_at, last_login_at
            FROM users
            WHERE wallet = ?
            """,
            (wallet,),
        ).fetchone()

    if not row:
        return jsonify({"user": None})

    return jsonify({"user": public_user_json(row)})


@app.post("/api/profile")
def update_profile():
    wallet = current_wallet()
    data = json_body()
    username = normalize_username(data.get("username"))
    avatar = data.get("avatar")

    avatar_values = (None, None)
    if avatar:
        avatar_values = decode_image_data_url(avatar, MAX_AVATAR_IMAGE_BYTES)

    try:
        with db() as conn:
            if avatar:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, avatar_mime = ?, avatar_blob = ?
                    WHERE wallet = ?
                    """,
                    (username, avatar_values[0], avatar_values[1], wallet),
                )
            else:
                conn.execute(
                    "UPDATE users SET username = ? WHERE wallet = ?",
                    (username, wallet),
                )
            row = conn.execute(
                """
                SELECT wallet, username, avatar_mime, avatar_blob,
                       created_at, last_login_at
                FROM users WHERE wallet = ?
                """,
                (wallet,),
            ).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": "This username is already taken."}), 409

    return jsonify({"user": public_user_json(row)})


@app.get("/api/users")
def users():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT
                u.wallet,
                u.username,
                u.avatar_mime,
                u.avatar_blob,
                u.created_at,
                u.last_login_at,
                COUNT(c.id) AS commission_count,
                MIN(c.id) AS first_commission_id
            FROM users u
            LEFT JOIN commissions c
                ON c.artist_wallet = u.wallet
            WHERE u.username IS NOT NULL
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
                "username": row["username"],
                "avatar_url": avatar_url(row["wallet"])
                if row["avatar_blob"] is not None
                else None,
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
            SELECT wallet, username, avatar_mime, avatar_blob,
                   created_at, last_login_at
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
            "user": public_user_json(user),
            "commissions": [commission_json(row) for row in rows],
        }
    )


@app.get("/api/profiles/<username>")
def profile_by_username(username):
    username = normalize_username(username)

    with db() as conn:
        user = conn.execute(
            """
            SELECT wallet, username, avatar_mime, avatar_blob,
                   created_at, last_login_at
            FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()

        if not user:
            return jsonify({"error": "Profile not found."}), 404

        rows = conn.execute(
            """
            SELECT * FROM commissions
            WHERE artist_wallet = ?
            ORDER BY id DESC
            """,
            (user["wallet"],),
        ).fetchall()

    return jsonify(
        {
            "user": public_user_json(user),
            "commissions": [commission_json(row) for row in rows],
        }
    )


@app.get("/api/users/<wallet>/avatar")
def user_avatar(wallet):
    wallet = normalize_wallet(wallet)
    with db() as conn:
        row = conn.execute(
            "SELECT avatar_mime, avatar_blob FROM users WHERE wallet = ?",
            (wallet,),
        ).fetchone()

    if not row or row["avatar_blob"] is None:
        return jsonify({"error": "Avatar not found."}), 404

    return Response(
        row["avatar_blob"],
        mimetype=row["avatar_mime"],
        headers={"Cache-Control": "public, max-age=300"},
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
    _, network = network_by_key(data.get("network"))

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
                network["chain_id"],
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

        network_match = network_by_chain_id(row["offer_chain_id"])
        if not network_match:
            return jsonify({"error": "Offer is for an unsupported chain."}), 409

        _, network = network_match
        amount = row["offer_amount_micros"]
        recipient = row["offer_recipient"]

    try:
        valid, receipt = verify_payment_receipt(
            network,
            tx_hash,
            actor,
            recipient,
            amount,
        )
    except Exception as exc:
        app.logger.exception("Payment RPC verification failed")
        return jsonify(
            {"error": f"{network['name']} RPC verification failed: {exc}"}
        ), 502

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


def main() -> int:
    if len(sys.argv) > 1:
        return cli()

    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
