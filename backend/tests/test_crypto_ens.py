"""Tests for the crypto_ens collector with respx-mocked HTTP."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.collectors.crypto_ens import CryptoENSCollector
from app.config import get_settings
from app.schemas import SearchInput


ETH_ADDR = "0x1111111111111111111111111111111111111111"
BTC_ADDR = "bc1qexampleexampleexampleexampleexampleabcd"


def _ensideas_payload(with_btc: bool = False) -> dict:
    out: dict = {
        "address": ETH_ADDR,
        "name": "alice.eth",
        "displayName": "alice.eth",
        "avatar": None,
    }
    if with_btc:
        out["records"] = {"com.bitcoin": BTC_ADDR}
    return out


def _etherscan_balance_payload() -> dict:
    return {"status": "1", "message": "OK", "result": "1234500000000000000"}  # 1.2345 ETH


def _etherscan_txlist_payload() -> dict:
    return {
        "status": "1",
        "message": "OK",
        "result": [
            {"timeStamp": "1577836800", "hash": "0xabc"},
            {"timeStamp": "1600000000", "hash": "0xdef"},
            {"timeStamp": "1700000000", "hash": "0xfee"},
        ],
    }


def _blockchair_payload() -> dict:
    return {
        "data": {
            BTC_ADDR: {
                "address": {
                    "balance": 250000000,  # 2.5 BTC in sat
                    "transaction_count": 7,
                }
            }
        }
    }


class _FakeSettings:
    def __init__(self, key: str = "fake-key"):
        self.etherscan_api_key = key


@pytest.fixture(autouse=True)
def _patch_etherscan_key(monkeypatch):
    """Replace get_settings used by the collector module."""
    from app.collectors import crypto_ens as mod

    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings("fake-key"))
    yield


@pytest.mark.asyncio
async def test_crypto_ens_full_flow_with_btc():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.ensideas.com/ens/resolve/alice.eth").mock(
            return_value=httpx.Response(200, json=_ensideas_payload(with_btc=True))
        )
        # Etherscan: route by query params via regex
        es = router.get(url__regex=r"https://api\.etherscan\.io/api.*")
        es.side_effect = [
            httpx.Response(200, json=_etherscan_balance_payload()),
            httpx.Response(200, json=_etherscan_txlist_payload()),
        ]
        router.get(
            f"https://api.blockchair.com/bitcoin/dashboards/address/{BTC_ADDR}"
        ).mock(return_value=httpx.Response(200, json=_blockchair_payload()))

        collector = CryptoENSCollector()
        findings = [
            f async for f in collector.run(SearchInput(username="alice"))
        ]

    kinds = {f.entity_type for f in findings}
    assert "ENSProfile" in kinds
    assert "EthereumBalance" in kinds
    assert "EthereumActivity" in kinds
    assert "BitcoinAddress" in kinds

    ens = next(f for f in findings if f.entity_type == "ENSProfile")
    assert ens.payload["ens_name"] == "alice.eth"
    assert ens.payload["address"] == ETH_ADDR

    bal = next(f for f in findings if f.entity_type == "EthereumBalance")
    assert abs(bal.payload["balance_eth"] - 1.2345) < 1e-9

    act = next(f for f in findings if f.entity_type == "EthereumActivity")
    assert act.payload["tx_count"] == 3
    assert act.payload["first_tx_ts"].startswith("2020-01-01")

    btc = next(f for f in findings if f.entity_type == "BitcoinAddress")
    assert btc.payload["balance_btc"] == 2.5
    assert btc.payload["tx_count"] == 7


@pytest.mark.asyncio
async def test_crypto_ens_skips_etherscan_without_key(monkeypatch):
    from app.collectors import crypto_ens as mod

    monkeypatch.setattr(mod, "get_settings", lambda: _FakeSettings(""))

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.ensideas.com/ens/resolve/alice.eth").mock(
            return_value=httpx.Response(200, json=_ensideas_payload())
        )
        # Any etherscan call would 500 — assert it's never made.
        es = router.get(url__regex=r"https://api\.etherscan\.io/api.*").mock(
            return_value=httpx.Response(500, json={})
        )

        collector = CryptoENSCollector()
        findings = [
            f async for f in collector.run(SearchInput(username="alice"))
        ]

    assert es.call_count == 0
    kinds = {f.entity_type for f in findings}
    assert kinds == {"ENSProfile"}


@pytest.mark.asyncio
async def test_crypto_ens_unresolved_yields_nothing():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.ensideas.com/ens/resolve/ghost.eth").mock(
            return_value=httpx.Response(200, json={"address": None, "name": None})
        )
        collector = CryptoENSCollector()
        findings = [
            f async for f in collector.run(SearchInput(username="ghost"))
        ]
    assert findings == []


@pytest.mark.asyncio
async def test_crypto_ens_no_username_yields_nothing():
    collector = CryptoENSCollector()
    findings = [f async for f in collector.run(SearchInput(full_name="Alice"))]
    assert findings == []


def test_collector_registered():
    from app.collectors.base import collector_registry

    assert collector_registry.by_name("crypto_ens") is CryptoENSCollector
