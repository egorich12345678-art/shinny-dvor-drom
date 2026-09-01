#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, json, base64, urllib.request, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"

TYPE_MAP = {
    "Легковая": "Легковая",
    "Легкогрузовая": "Легковая",
    "Грязевая": "Легковая",
    "SUV": "Легковая",
    "Грузовая": "Грузовая",
    "Сельхоз": "Спецтехническая",
    "Индустриальная": "Спецтехническая",
}

MIN_REST = 4
SET_SIZE = 4

def auth_header(user, password):
    tok = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {tok}"

def parse_price(v):
    s = str(v or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
        return d if d > 0 else None
    except InvalidOperation:
        return None

def fetch():
    user = os.environ.get("SIBZAPASKA_USER","")
    password = os.environ.get("SIBZAPASKA_PASSWORD","")
    if not user or not password:
        raise RuntimeError("Не заданы SIBZAPASKA_USER / SIBZAPASKA_PASSWORD")
    req = urllib.request.Request(API_URL, headers={
        "Authorization": auth_header(user, password),
        "Accept":"application/json",
        "User-Agent":"Shinny-Dvor-Zapaska/1.0",
    })
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8-sig"))

def season_ru(v):
    s = str(v or "").strip().lower()
    if s == "зима": return "Зимняя"
    if s == "лето": return "Летняя"
    return s.title() if s else ""

def build_marking(x):
    cat = x.get("category","")
    name = str(x.get("name") or "")
    width = str(x.get("width") or "").strip()
    height = str(x.get("height") or "").strip()
    diameter = str(x.get("diameter") or "").strip()
    load = str(x.get("load_index") or "").strip()
    speed = str(x.get("speed_index") or "").strip()

    # Для грязевых/части спецшин надежнее брать размер из исходного name.
    m = re.match(r"^\s*([0-9.,xX/\-RrC LT]+)", name)
    if cat in {"Грязевая","Сельхоз","Индустриальная"} and m:
        size = m.group(1).strip().replace(" ", "")
    else:
        if width and height:
            suffix = "C" if cat == "Легкогрузовая" and not diameter.upper().endswith("C") else ""
            size = f"{width}/{height}R{diameter}{suffix}"
        elif width and diameter:
            size = f"{width}R{diameter}"
        elif m:
            size = m.group(1).strip().replace(" ", "")
        else:
            size = ""

    idx = f"{load}{speed}".strip()
    marking = " ".join(p for p in [size, idx] if p)

    # Слойность и TL/TT из исходного названия
    pr = re.search(r"\b(\d{1,2}PR)\b", name, flags=re.I)
    if pr:
        marking += f" {pr.group(1).upper()}"
    tt = re.search(r"\b(TL|TT)\b", name, flags=re.I)
    if tt:
        marking += f" {tt.group(1).upper()}"
    if re.search(r"\bXL\b", name, flags=re.I):
        marking += " XL"
    return re.sub(r"\s+", " ", marking).strip()

def add(parent, tag, val):
    if val is None or val == "":
        return
    e = ET.SubElement(parent, tag)
    e.text = str(val)

def main():
    out = Path(sys.argv[1] if len(sys.argv)>1 else "public/drom_zapaska.xml")
    data = fetch()
    if not isinstance(data, list):
        raise RuntimeError("API Запаски вернул неожиданный JSON")

    root = ET.Element("offers")
    stats = {"source":len(data), "written":0, "skip_rest":0, "skip_price":0, "skip_type":0}

    seen = set()
    for x in data:
        try:
            rest = int(x.get("rest") or 0)
        except:
            rest = 0
        if rest < MIN_REST:
            stats["skip_rest"] += 1
            continue

        drom_type = TYPE_MAP.get(str(x.get("category") or "").strip())
        if not drom_type:
            stats["skip_type"] += 1
            continue

        retail = parse_price(x.get("retail"))
        if retail is None:
            stats["skip_price"] += 1
            continue

        brand = str(x.get("brand") or "").strip()
        model = str(x.get("model") or "").strip()
        cae = str(x.get("cae") or x.get("article") or "").strip()
        marking = build_marking(x)
        season = season_ru(x.get("season"))
        if not all([brand, model, cae, marking, season]):
            continue

        # Дедупликация по ключу, понятному Дрому
        stud = str(x.get("studded") or "").strip()
        if season == "Зимняя":
            spike = "Шипованная" if stud == "Да" else "Нешипуемая"
        else:
            spike = ""

        key = (brand.casefold(), model.casefold(), marking.casefold(), spike.casefold())
        if key in seen:
            continue
        seen.add(key)

        price_set = int((retail * SET_SIZE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        o = ET.SubElement(root, "offer")
        add(o, "name", f"Автошины {brand} {str(x.get('name') or model).strip()}")
        add(o, "available", "false")
        add(o, "model", f"{brand} {model}".strip())
        add(o, "marking", marking)
        add(o, "inSet", SET_SIZE)
        add(o, "quantity", rest)
        add(o, "price", price_set)
        add(o, "condition", "Новая")
        add(o, "season", season)
        add(o, "type", drom_type)
        add(o, "picture", str(x.get("img") or "").strip())
        add(o, "spike", spike)
        add(o, "cae", cae)
        add(o, "article", str(x.get("article") or "").strip())
        stats["written"] += 1

    if stats["written"] < 100:
        raise RuntimeError(f"Слишком мало товаров: {stats['written']}. Публикация остановлена.")

    tree = ET.ElementTree(root)
    try: ET.indent(tree, space="  ")
    except: pass

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".zapaska_", suffix=".xml", dir=str(out.parent))
    os.close(fd)
    try:
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        ET.parse(tmp)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(json.dumps(stats, ensure_ascii=False))
    print(f"Файл: {out}")

if __name__ == "__main__":
    main()
