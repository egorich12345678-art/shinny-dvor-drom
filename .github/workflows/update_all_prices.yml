#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Объединяет два готовых XML-прайса Дром:
- 4tochki
- Запаска

Результат: один drom_tires.xml без пересекающегося ассортимента.

Правила:
1. Сначала ищем один и тот же товар по идентификаторам производителя:
   CAE / article / EAN (в пределах бренда).
2. Если идентификаторы не совпали — сравниваем модель + размер +
   индекс нагрузки/скорости + шипованность + RunFlat + тип.
3. Если товар есть у обоих поставщиков:
   - в итоговый прайс попадает только одна карточка;
   - цена берётся МАКСИМАЛЬНАЯ из двух прайсов, чтобы итоговая цена
     не оказалась ниже МРЦ любого из поставщиков;
   - остаток берётся максимальный, а не суммируется;
   - недостающие поля дополняются из второй карточки.
4. Не добавляет служебные теги в XML, чтобы не ломать формат Дрома.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from copy import deepcopy
from typing import Dict, List, Tuple


def get_text(offer: ET.Element, tag: str) -> str:
    el = offer.find(tag)
    return (el.text or "").strip() if el is not None else ""


def set_text(offer: ET.Element, tag: str, value) -> None:
    el = offer.find(tag)
    if el is None:
        el = ET.SubElement(offer, tag)
    el.text = str(value)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value)
    return " ".join(value.split())


def normalize_id(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-zа-я]+", "", value)


def brand_from_model(offer: ET.Element) -> str:
    # Первый токен model в наших конвертерах — бренд.
    model = get_text(offer, "model")
    first = model.split()[0] if model else ""
    return normalize_text(first)


def product_ids(offer: ET.Element) -> List[Tuple[str, str]]:
    """
    CAE одного источника нередко равен article другого источника,
    поэтому тип идентификатора специально НЕ входит в ключ.
    Бренд добавляем, чтобы случайные короткие номера разных марок
    не склеились между собой.
    """
    brand = brand_from_model(offer)
    result = []
    for tag in ("cae", "article", "EAN"):
        value = normalize_id(get_text(offer, tag))
        if value:
            result.append((brand, value))
    return result


def marking_key(marking: str) -> Tuple[str, str]:
    s = (marking or "").upper().replace("ZR", "R")
    # TL/TT/XL сами по себе не должны создавать вторую карточку товара.
    # RunFlat учитывается отдельным полем.
    s = re.sub(r"\b(?:TL|TT|XL|RF|RFT)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Типичные размеры: 225/55R18, 195/75R16C, 31X10.5R15.
    size_re = (
        r"(\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?R\d+(?:[.,]\d+)?C?"
        r"|\d+(?:[.,]\d+)?X\d+(?:[.,]\d+)?R\d+(?:[.,]\d+)?)"
    )
    m = re.search(size_re, s)
    size = m.group(1).replace(",", ".") if m else ""

    rest = s[m.end():] if m else s
    # 98H / 107/105R и т.п. Не путаем с 8PR/10PR.
    idx = re.search(r"\b(\d{2,3}(?:/\d{2,3})?[A-Z])\b", rest)
    load_speed = idx.group(1) if idx else ""
    return size, load_speed


def semantic_key(offer: ET.Element) -> Tuple[str, ...]:
    size, load_speed = marking_key(get_text(offer, "marking"))
    return (
        normalize_text(get_text(offer, "model")),
        size,
        load_speed,
        normalize_text(get_text(offer, "spike")),
        normalize_text(get_text(offer, "runflat")),
        normalize_text(get_text(offer, "type")),
    )


def as_int(value: str) -> int:
    try:
        return int(float((value or "0").replace(",", ".")))
    except Exception:
        return 0


def merge_duplicate(current: ET.Element, incoming: ET.Element) -> None:
    # Цена: максимум из двух рекомендованных цен.
    set_text(
        current,
        "price",
        max(as_int(get_text(current, "price")), as_int(get_text(incoming, "price"))),
    )

    # Остаток не суммируем: это защищает от завышения доступного количества.
    set_text(
        current,
        "quantity",
        max(as_int(get_text(current, "quantity")), as_int(get_text(incoming, "quantity"))),
    )

    # Если в основной карточке чего-то нет, дополняем из второй.
    for child in incoming:
        incoming_value = (child.text or "").strip()
        if incoming_value and not get_text(current, child.tag):
            set_text(current, child.tag, incoming_value)


def merge_prices(fourtochki: str, zapaska: str):
    selected: List[ET.Element] = []
    by_id: Dict[Tuple[str, str], int] = {}
    by_semantic: Dict[Tuple[str, ...], int] = {}

    stats = {
        "4tochki": 0,
        "zapaska": 0,
        "duplicates_by_id": 0,
        "duplicates_by_semantic": 0,
    }

    for source_name, path in (("4tochki", fourtochki), ("zapaska", zapaska)):
        root = ET.parse(path).getroot()
        offers = root.findall("offer")
        stats[source_name] = len(offers)

        for offer in offers:
            ids = product_ids(offer)
            sem = semantic_key(offer)
            existing_index = None
            match_kind = None

            # Самый надёжный вариант: одинаковый артикул/CAE/EAN.
            for product_id in ids:
                if product_id in by_id:
                    existing_index = by_id[product_id]
                    match_kind = "id"
                    break

            # Запасной вариант: характеристики, по которым Дром обычно
            # распознаёт одну и ту же товарную карточку.
            if (
                existing_index is None
                and sem[0]
                and sem[1]
                and sem in by_semantic
            ):
                existing_index = by_semantic[sem]
                match_kind = "semantic"

            if existing_index is None:
                existing_index = len(selected)
                selected.append(deepcopy(offer))
                by_semantic.setdefault(sem, existing_index)
            else:
                merge_duplicate(selected[existing_index], offer)
                if match_kind == "id":
                    stats["duplicates_by_id"] += 1
                else:
                    stats["duplicates_by_semantic"] += 1
                by_semantic.setdefault(sem, existing_index)

            for product_id in ids:
                by_id[product_id] = existing_index

    stats["duplicates_total"] = (
        stats["duplicates_by_id"] + stats["duplicates_by_semantic"]
    )
    stats["merged"] = len(selected)
    return selected, stats


def write_xml(offers: List[ET.Element], output: str) -> None:
    root = ET.Element("offers")
    for offer in offers:
        root.append(offer)

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    output = os.path.abspath(output)
    out_dir = os.path.dirname(output) or "."
    os.makedirs(out_dir, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(prefix=".merged_", suffix=".xml", dir=out_dir)
    os.close(fd)
    try:
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        # Финальная проверка XML до публикации.
        ET.parse(temp_path)
        os.replace(temp_path, output)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge 4tochki + Zapaska Drom XML")
    parser.add_argument("--fourtochki", required=True)
    parser.add_argument("--zapaska", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-offers", type=int, default=100)
    args = parser.parse_args()

    offers, stats = merge_prices(args.fourtochki, args.zapaska)

    if len(offers) < args.min_offers:
        raise SystemExit(
            f"ОШИБКА: после объединения только {len(offers)} товаров. "
            "Публикация остановлена."
        )

    write_xml(offers, args.output)

    print("Объединённый прайс готов")
    print(f"4tochki: {stats['4tochki']}")
    print(f"Запаска: {stats['zapaska']}")
    print(f"Дубли по артикулу/CAE/EAN: {stats['duplicates_by_id']}")
    print(f"Дубли по характеристикам: {stats['duplicates_by_semantic']}")
    print(f"Всего удалено дублей: {stats['duplicates_total']}")
    print(f"Итоговых товаров: {stats['merged']}")
    print(f"Файл: {os.path.abspath(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
