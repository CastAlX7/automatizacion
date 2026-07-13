from __future__ import annotations


def build_mock_urls(car_id: str) -> list[str]:
    return [
        f"https://marketplace.facebook.com/item/{car_id}",
        f"https://www.mercadolibre.com/item/{car_id}",
        f"https://www.instagram.com/p/{car_id}/",
    ]
