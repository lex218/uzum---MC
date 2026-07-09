"""Тесты HTTP-клиентов на respx: заголовки, пагинация, retry."""
import httpx
import pytest
import respx

from moysklad.client import MoySkladClient, MoySkladError
from uzum.client import UzumClient

UZ = "https://api-seller.uzum.uz/api/seller-openapi"
MS = "https://api.moysklad.ru/api/remap/1.2"


@respx.mock
def test_uzum_auth_header_without_bearer():
    route = respx.get(f"{UZ}/v1/shops").mock(
        return_value=httpx.Response(200, json=[{"id": 387, "name": "ComFore"}])
    )
    client = UzumClient("TOKEN123")
    assert client.default_shop_id() == 387
    assert route.calls[0].request.headers["Authorization"] == "TOKEN123"


@respx.mock
def test_uzum_finance_orders_pagination_and_seconds():
    def responder(request):
        page = int(request.url.params["page"])
        assert request.url.params["dateFrom"] == "1700000000"  # секунды, не мс
        items = [
            {"id": page * 50 + i, "orderId": 1, "status": "PROCESSING", "date": 1}
            for i in range(50 if page == 0 else 3)
        ]
        return httpx.Response(200, json={"orderItems": items, "totalElements": 53})

    respx.get(f"{UZ}/v1/finance/orders").mock(side_effect=responder)
    client = UzumClient("t")
    items = list(client.finance_orders(387, date_from_s=1700000000))
    assert len(items) == 53


@respx.mock
def test_uzum_retry_on_429(monkeypatch):
    monkeypatch.setattr("uzum.client.time.sleep", lambda s: None)
    route = respx.get(f"{UZ}/v1/shops")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=[{"id": 1, "name": "S"}]),
    ]
    assert UzumClient("t").shops() == [{"id": 1, "name": "S"}]
    assert route.call_count == 2


@respx.mock
def test_ms_gzip_header_and_error(monkeypatch):
    monkeypatch.setattr("moysklad.client.time.sleep", lambda s: None)
    route = respx.get(f"{MS}/entity/organization").mock(
        return_value=httpx.Response(
            412, json={"errors": [{"error": "Поле не задано", "code": 3000}]}
        )
    )
    client = MoySkladClient("tok")
    with pytest.raises(MoySkladError, match="Поле не задано"):
        client.get("/entity/organization")
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok"
    assert "gzip" in req.headers["Accept-Encoding"]


@respx.mock
def test_ms_retry_uses_lognex_header(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("moysklad.client.time.sleep", sleeps.append)
    route = respx.get(f"{MS}/entity/store")
    route.side_effect = [
        httpx.Response(429, headers={"X-Lognex-Retry-After": "1500"}),
        httpx.Response(200, json={"rows": []}),
    ]
    assert MoySkladClient("t").rows("/entity/store") == []
    assert sleeps == [1.5]
