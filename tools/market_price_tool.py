from __future__ import annotations

from datetime import datetime
from typing import Any


def estimate_market_price(car_data: dict[str, Any]) -> float:
    year = car_data.get("año") or car_data.get("anio")
    km = car_data.get("km")
    if year is None or km is None:
        raise ValueError("car_data debe incluir año/anio y km")

    year = int(year)
    km = float(km)

    base_prices: dict[tuple[str, str], float] = {
        ("Toyota", "Corolla"): 16000.0,
        ("Honda", "Civic"): 15000.0,
        ("Ford", "F-150"): 22000.0,
    }

    marca = str(car_data.get("marca", "")).strip() or "Generic"
    modelo = str(car_data.get("modelo", "")).strip() or "Car"
    base = base_prices.get((marca, modelo), 14000.0)

    current_year = datetime.now().year
    age = max(0, current_year - year)
    depreciation = min(0.75, age * 0.06)

    km_penalty = min(0.60, (km / 10000.0) * 0.005)

    price = base * (1.0 - depreciation - km_penalty)
    return max(500.0, round(price, 2))
