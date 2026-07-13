from __future__ import annotations


def upload_images_mock(car_id: str, image_paths: list[str]) -> list[str]:
    return [f"https://cdn.example.com/cars/{car_id}/{i}.jpg" for i, _ in enumerate(image_paths, start=1)]
