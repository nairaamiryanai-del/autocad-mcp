"""Каталог профилей: артикул, назначение, глубина, момент инерции.

Нужен для артикулов на чертежах, спецификации и подбора стойки в статическом расчёте.
Встроенные записи взяты из примера проекта (стойка 175 мм ALM150175, Ix = 467 см4).
У остальных артикулов из примера назначение и Ix не известны: их нужно заполнить по каталогу
системодателя (set_profile), иначе расчёт их не выберет. Свой каталог хранится в JSON рядом с файлами вывода.
"""

from __future__ import annotations

import json
import os
from typing import Any

ROLES = ("стойка", "ригель", "стропило", "рама", "импост", "створка", "прижим", "крышка", "прочее")

BUILTIN: list[dict[str, Any]] = [
    {"article": "ALM150175", "name": "Стойка 175 мм", "role": "стойка", "depth_mm": 175, "ix_cm4": 467,
     "source": "пример проекта, лист 3"},
    {"article": "ALM150075", "name": "Ригель 75 мм", "role": "ригель", "depth_mm": 75, "ix_cm4": None,
     "source": "пример проекта, лист 2; Ix уточнить по каталогу"},
    {"article": "ALM750509", "name": "Профиль ALM750509", "role": "прочее", "depth_mm": None, "ix_cm4": None,
     "source": "пример проекта, лист 1; назначение уточнить"},
    {"article": "ALM750212", "name": "Профиль ALM750212", "role": "прочее", "depth_mm": None, "ix_cm4": None,
     "source": "пример проекта, лист 1; назначение уточнить"},
    {"article": "ALM750504", "name": "Профиль ALM750504", "role": "прочее", "depth_mm": None, "ix_cm4": None,
     "source": "пример проекта, лист 1; назначение уточнить"},
]


def _path(out_dir: str) -> str:
    return os.path.join(out_dir, "profiles.json")


def load(out_dir: str) -> list[dict[str, Any]]:
    """Свой каталог поверх встроенного: записи с тем же артикулом заменяют встроенные."""
    items = {p["article"]: dict(p, builtin=True) for p in BUILTIN}
    path = _path(out_dir)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for p in json.load(f):
                items[p["article"]] = dict(p, builtin=False)
    return list(items.values())


def save_one(out_dir: str, profile: dict[str, Any]) -> dict[str, Any]:
    art = str(profile.get("article") or "").strip()
    if not art:
        raise ValueError("Нужен артикул профиля")
    role = str(profile.get("role") or "прочее")
    if role not in ROLES:
        raise ValueError(f"Назначение: одно из {', '.join(ROLES)}")
    path = _path(out_dir)
    own: list[dict[str, Any]] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            own = json.load(f)
    rec = {"article": art, "name": str(profile.get("name") or art), "role": role,
           "depth_mm": profile.get("depth_mm"), "ix_cm4": profile.get("ix_cm4"), "source": profile.get("source") or "введено вручную"}
    own = [p for p in own if p["article"] != art] + [rec]
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(own, f, ensure_ascii=False, indent=2)
    return rec


def delete_one(out_dir: str, article: str) -> bool:
    path = _path(out_dir)
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        own = json.load(f)
    rest = [p for p in own if p["article"] != article]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rest, f, ensure_ascii=False, indent=2)
    return len(rest) != len(own)


def find(out_dir: str, article: str) -> dict[str, Any] | None:
    return next((p for p in load(out_dir) if p["article"] == article), None)
