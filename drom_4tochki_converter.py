#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Шинный двор: автоматический прайс B2B 4tochki -> Дром.

Правила:
- склад только Новосибирск: rest_novosib3;
- остаток >= 4 шин;
- только легковые шины;
- продаём комплектом по 4 шт.;
- цена комплекта = price_mits * 4;
- позиции без МИЦ не выгружаются;
- "более 40" трактуется как 40;
- статус по умолчанию "Под заказ" (available=false);
- защита от публикации подозрительно пустого прайса.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, Optional, Tuple

DEFAULT_OUTPUT = "drom_tires.xml"
DEFAULT_MIN_REST = 4
DEFAULT_SET_SIZE = 4
DEFAULT_TIRE_TYPE = "Легковая"
DEFAULT_MIN_OFFERS = 100


@dataclass
class Offer:
    cae: str
    name: str
    model: str
    marking: str
    quantity: int
    price: int
    season: str
    tire_type: str
    picture: str
    spike: str
    runflat: str
    homologation: str
    ean: str
    mits_unit: Decimal


def text_map(elem: ET.Element) -> Dict[str, str]:
    return {child.tag: (child.text or "").strip() for child in elem}


def parse_rest(value: str) -> int:
    value = (value or "").strip().lower()
    if not value:
        return 0
    nums = re.findall(r"\d+", value)
    return int(nums[0]) if nums else 0


def parse_decimal(value: str) -> Optional[Decimal]:
    value = (value or "").strip().replace(" ", "").replace(",", ".")
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def normalize_num(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        d = Decimal(value.replace(",", "."))
        s = format(d.normalize(), "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    except InvalidOperation:
        return value


def build_marking(d: Dict[str, str]) -> str:
    width = normalize_num(d.get("width", ""))
    height = normalize_num(d.get("height", ""))
    diameter = (d.get("diameter") or "").strip()

    if width and height and height not in {"0", "0.0"}:
        size = f"{width}/{height}{diameter}"
    elif width:
        size = f"{width}{diameter}"
    else:
        size = diameter

    load_speed = f"{(d.get('load_index') or '').strip()}{(d.get('speed_index') or '').strip()}"
    parts = [size]
    if load_speed:
        parts.append(load_speed)

    camera = (d.get("camera") or "").strip().upper()
    if camera in {"TL", "TT"}:
        parts.append(camera)

    if (d.get("tonnage") or "").strip().lower() == "да":
        parts.append("XL")

    return " ".join(p for p in parts if p)


def source_name(d: Dict[str, str]) -> str:
    brand = (d.get("brand") or "").strip()
    original = (d.get("name") or "").strip()
    if original:
        return f"Автошины {brand} {original}".strip()
    model = (d.get("model") or "").strip()
    return f"Автошины {brand} {model}".strip()


def build_offer(d: Dict[str, str], min_rest: int, set_size: int, tire_type: str) -> Optional[Offer]:
    quantity = parse_rest(d.get("rest_novosib3", ""))
    if quantity < min_rest:
        return None

    if (d.get("tiretype") or "").strip() != tire_type:
        return None

    mits = parse_decimal(d.get("price_mits", ""))
    if mits is None or mits <= 0:
        return None

    brand = (d.get("brand") or "").strip()
    model_name = (d.get("model") or "").strip()
    cae = (d.get("cae") or "").strip()
    season = (d.get("season") or "").strip()
    marking = build_marking(d)

    if not all([cae, brand, model_name, marking, season]):
        return None

    set_price = int((mits * set_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    picture = (d.get("img_big") or d.get("img_big_pish") or d.get("img_small") or "").strip()
    thorn = (d.get("thorn") or "").strip().lower()
    spike = "Шипованная" if thorn == "да" else ""
    runflat = "Да" if (d.get("runflat") or "").strip().lower() == "да" else ""
    homologation = (d.get("omolog") or "").strip()
    ean = (d.get("gtin") or "").split(",")[0].strip()

    return Offer(
        cae=cae,
        name=source_name(d),
        model=f"{brand} {model_name}".strip(),
        marking=marking,
        quantity=quantity,
        price=set_price,
        season=season,
        tire_type=tire_type,
        picture=picture,
        spike=spike,
        runflat=runflat,
        homologation=homologation,
        ean=ean,
        mits_unit=mits,
    )


def dedupe_key(o: Offer) -> Tuple[str, str, str, str]:
    return (o.model.casefold(), o.marking.casefold(), o.spike.casefold(), o.runflat.casefold())


def choose_better(a: Offer, b: Offer) -> Offer:
    if b.mits_unit < a.mits_unit:
        return b
    if b.mits_unit > a.mits_unit:
        return a
    if b.quantity > a.quantity:
        return b
    return a


def open_source(source: str):
    if re.match(r"^https?://", source, flags=re.I):
        req = urllib.request.Request(
            source,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Shinny-Dvor-Drom-Feed/1.0)"},
        )
        return urllib.request.urlopen(req, timeout=180)
    return open(source, "rb")


def load_offers(source: str, min_rest: int, set_size: int, tire_type: str):
    stats = {"source_positions": 0, "eligible_before_dedupe": 0, "duplicates_removed": 0}
    selected: Dict[Tuple[str, str, str, str], Offer] = {}

    with open_source(source) as src:
        for _, elem in ET.iterparse(src, events=("end",)):
            if elem.tag != "tires":
                continue
            stats["source_positions"] += 1
            d = text_map(elem)
            offer = build_offer(d, min_rest, set_size, tire_type)
            if offer:
                stats["eligible_before_dedupe"] += 1
                key = dedupe_key(offer)
                if key in selected:
                    stats["duplicates_removed"] += 1
                    selected[key] = choose_better(selected[key], offer)
                else:
                    selected[key] = offer
            elem.clear()

    offers = sorted(selected.values(), key=lambda o: (o.model.casefold(), o.marking.casefold(), o.cae))
    stats["offers_written"] = len(offers)
    return offers, stats


def sub(parent: ET.Element, tag: str, value) -> None:
    if value is None or value == "":
        return
    el = ET.SubElement(parent, tag)
    el.text = str(value)


def build_tree(offers: Iterable[Offer], set_size: int, available: bool) -> ET.ElementTree:
    root = ET.Element("offers")
    for o in offers:
        offer = ET.SubElement(root, "offer")
        sub(offer, "name", o.name)
        sub(offer, "available", "true" if available else "false")
        sub(offer, "model", o.model)
        sub(offer, "marking", o.marking)
        sub(offer, "inSet", set_size)
        sub(offer, "quantity", o.quantity)
        sub(offer, "price", o.price)
        sub(offer, "condition", "Новая")
        sub(offer, "season", o.season)
        sub(offer, "type", o.tire_type)
        sub(offer, "picture", o.picture)
        sub(offer, "spike", o.spike)
        sub(offer, "runflat", o.runflat)
        sub(offer, "homologation", o.homologation)
        sub(offer, "EAN", o.ean)
        sub(offer, "cae", o.cae)

    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    return tree


def write_atomic(tree: ET.ElementTree, output: str) -> None:
    output = os.path.abspath(output)
    out_dir = os.path.dirname(output) or "."
    os.makedirs(out_dir, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix=".drom_tires_", suffix=".xml", dir=out_dir)
    os.close(fd)
    try:
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        ET.parse(tmp)
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser(description="Шинный двор: 4tochki -> Drom XML")
    parser.add_argument(
        "--source",
        default=os.environ.get("FOURTOCHKI_URL", ""),
        help="URL пользовательской XML-выгрузки 4tochki. Можно задать через FOURTOCHKI_URL.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--min-rest", type=int, default=DEFAULT_MIN_REST)
    parser.add_argument("--set-size", type=int, default=DEFAULT_SET_SIZE)
    parser.add_argument("--tire-type", default=DEFAULT_TIRE_TYPE)
    parser.add_argument("--min-offers", type=int, default=DEFAULT_MIN_OFFERS)
    parser.add_argument("--available", choices=["true", "false"], default="false")
    args = parser.parse_args()

    if not args.source:
        print("ОШИБКА: не задан источник 4tochki. Укажите --source или FOURTOCHKI_URL.", file=sys.stderr)
        return 2

    try:
        offers, stats = load_offers(args.source, args.min_rest, args.set_size, args.tire_type)
        if len(offers) < args.min_offers:
            raise RuntimeError(
                f"Получено только {len(offers)} позиций (< {args.min_offers}). "
                "Прайс не опубликован. Проверьте выгрузку 4tochki."
            )
        tree = build_tree(offers, args.set_size, available=(args.available == "true"))
        write_atomic(tree, args.output)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    print("Готово")
    print(f"Исходных позиций: {stats['source_positions']}")
    print(f"Прошли фильтры: {stats['eligible_before_dedupe']}")
    print(f"Удалено дублей: {stats['duplicates_removed']}")
    print(f"Записано предложений: {stats['offers_written']}")
    print(f"Файл: {os.path.abspath(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
