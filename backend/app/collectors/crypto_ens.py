"""Crypto / ENS collector.

Resolves an ENS name (``{username}.eth`` or any ``*.eth`` provided as
username) via the free ensideas resolver and, if an ETH address is
found, queries Etherscan for balance + transactions. If a BTC address is
exposed by the ENS text records, also fetches Blockchair stats.

Inputs:
  - username  (used as ``{username}.eth`` if it does not already contain a dot)
  - full_name (only used as a hint, no direct lookup)

Findings emit (entity_type / payload):
  - ENSProfile         -> {ens_name, address}
  - EthereumBalance    -> {address, balance_eth}
  - EthereumActivity   -> {address, first_tx_ts, tx_count}
  - BitcoinAddress     -> {btc_address, balance_btc, tx_count}
"""
from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.collectors.base import Collector, Finding, register
from app.config import get_settings
from app.http_util import client
from app.schemas import SearchInput


ENSIDEAS_URL = "https://api.ensideas.com/ens/resolve/{name}"
ETHERSCAN_URL = "https://api.etherscan.io/api"
BLOCKCHAIR_URL = "https://api.blockchair.com/bitcoin/dashboards/address/{addr}"


@register
class CryptoENSCollector(Collector):
    name = "crypto_ens"
    category = "crypto"
    needs = ("username", "full_name")
    timeout_seconds = 30
    description = "ENS resolve + Etherscan/Blockchair balances and tx stats."

    async def run(self, input: SearchInput) -> AsyncIterator[Finding]:
        ens_name = _ens_candidate(input)
        if not ens_name:
            return

        s = get_settings()
        etherscan_key = getattr(s, "etherscan_api_key", "") or ""

        async with client(timeout=20) as c:
            resolved = await _resolve_ens(c, ens_name)
            if not resolved:
                return

            eth_addr: str | None = resolved.get("address")
            btc_addr: str | None = _extract_btc(resolved)

            yield Finding(
                collector=self.name,
                category="crypto",
                entity_type="ENSProfile",
                title=f"ENS: {ens_name}"
                + (f" -> {eth_addr}" if eth_addr else ""),
                url=f"https://app.ens.domains/name/{ens_name}",
                confidence=0.9 if eth_addr else 0.6,
                payload={
                    "ens_name": ens_name,
                    "address": eth_addr,
                    "displayName": resolved.get("displayName"),
                    "avatar": resolved.get("avatar"),
                },
            )

            if eth_addr and etherscan_key:
                async for f in _etherscan_track(c, eth_addr, etherscan_key, self.name):
                    yield f

            if btc_addr:
                async for f in _blockchair_track(c, btc_addr, self.name):
                    yield f


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ens_candidate(i: SearchInput) -> str | None:
    if not i.username:
        return None
    u = i.username.strip().lower().lstrip("@")
    if not u:
        return None
    if "." in u:
        # Accept any *.eth form provided directly.
        return u if u.endswith(".eth") else None
    return f"{u}.eth"


async def _resolve_ens(c: httpx.AsyncClient, name: str) -> dict[str, Any] | None:
    try:
        r = await c.get(ENSIDEAS_URL.format(name=name))
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("address"):
        return None
    return data


def _extract_btc(resolved: dict[str, Any]) -> str | None:
    """Best-effort: ensideas may return text records that include a btc addr."""
    records = resolved.get("records") or resolved.get("texts") or {}
    if isinstance(records, dict):
        for k in ("com.bitcoin", "BTC", "btc", "bitcoin"):
            v = records.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    btc = resolved.get("btc_address") or resolved.get("bitcoin")
    if isinstance(btc, str) and btc.strip():
        return btc.strip()
    return None


async def _etherscan_track(
    c: httpx.AsyncClient, addr: str, key: str, collector_name: str
) -> AsyncIterator[Finding]:
    # Balance
    balance_eth: float | None = None
    try:
        r = await c.get(
            ETHERSCAN_URL,
            params={"module": "account", "action": "balance", "address": addr, "apikey": key},
        )
        if r.status_code == 200:
            j = r.json()
            if str(j.get("status")) == "1" or j.get("message") == "OK":
                try:
                    balance_eth = int(j.get("result", "0")) / 1e18
                except (TypeError, ValueError):
                    balance_eth = None
    except httpx.HTTPError:
        balance_eth = None

    if balance_eth is not None:
        yield Finding(
            collector=collector_name,
            category="crypto",
            entity_type="EthereumBalance",
            title=f"ETH balance {balance_eth:.6f} ({addr})",
            url=f"https://etherscan.io/address/{addr}",
            confidence=0.95,
            payload={"address": addr, "balance_eth": balance_eth},
        )

    # Tx list
    try:
        r = await c.get(
            ETHERSCAN_URL,
            params={
                "module": "account",
                "action": "txlist",
                "address": addr,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 100,
                "sort": "asc",
                "apikey": key,
            },
        )
    except httpx.HTTPError:
        return
    if r.status_code != 200:
        return
    try:
        j = r.json()
    except ValueError:
        return
    txs = j.get("result") if isinstance(j, dict) else None
    if not isinstance(txs, list) or not txs:
        return

    first_ts_raw = txs[0].get("timeStamp")
    first_tx_ts: str | None = None
    try:
        if first_ts_raw is not None:
            first_tx_ts = dt.datetime.fromtimestamp(
                int(first_ts_raw), tz=dt.timezone.utc
            ).isoformat()
    except (TypeError, ValueError):
        first_tx_ts = None

    yield Finding(
        collector=collector_name,
        category="crypto",
        entity_type="EthereumActivity",
        title=f"ETH activity: {len(txs)} tx (first {first_tx_ts})",
        url=f"https://etherscan.io/address/{addr}",
        confidence=0.9,
        payload={
            "address": addr,
            "tx_count": len(txs),
            "first_tx_ts": first_tx_ts,
        },
    )


async def _blockchair_track(
    c: httpx.AsyncClient, addr: str, collector_name: str
) -> AsyncIterator[Finding]:
    try:
        r = await c.get(BLOCKCHAIR_URL.format(addr=addr))
    except httpx.HTTPError:
        return
    if r.status_code != 200:
        return
    try:
        j = r.json()
    except ValueError:
        return
    data = (j or {}).get("data") or {}
    node = data.get(addr) if isinstance(data, dict) else None
    if not isinstance(node, dict):
        return
    addr_block = node.get("address") or {}
    balance_sat = addr_block.get("balance")
    tx_count = addr_block.get("transaction_count")
    balance_btc: float | None
    try:
        balance_btc = (int(balance_sat) / 1e8) if balance_sat is not None else None
    except (TypeError, ValueError):
        balance_btc = None
    yield Finding(
        collector=collector_name,
        category="crypto",
        entity_type="BitcoinAddress",
        title=f"BTC {addr}: bal={balance_btc} tx={tx_count}",
        url=f"https://blockchair.com/bitcoin/address/{addr}",
        confidence=0.85,
        payload={
            "btc_address": addr,
            "balance_btc": balance_btc,
            "tx_count": tx_count,
        },
    )


__all__ = ["CryptoENSCollector"]
