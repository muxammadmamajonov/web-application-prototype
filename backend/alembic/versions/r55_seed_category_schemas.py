"""Seed per-category attribute + identifier schemas for the whole catalog (R55)

Professional, category-specific schemas. Conventions:
* ``variant=True`` fields are the per-model SKU axes (the platform sets which
  options a model ships in; the seller picks one). ``color`` uses the global
  palette; numeric variants (storage, RAM, capacity…) take free values.
* ``variant=False`` fields are descriptive specs.
* Identifiers are per-unit IDs the seller captures at sale time — all optional.
* "Holati" (new/used) is added to durable goods (resale market).

Idempotent: overwrites each category's schema by slug.

Revision ID: r55categoryschemas
Revises: r54kidsbrands
Create Date: 2026-06-01 14:00:00+00:00
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "r55categoryschemas"
down_revision = "r54kidsbrands"
branch_labels = None
depends_on = None


def A(key, uz, ru, typ, variant=False, required=False, unit=None, values=None):
    f = {"key": key, "label_uz": uz, "label_ru": ru, "type": typ,
         "variant": variant, "required": required,
         "filled_by": "platform" if variant else "tenant"}
    if unit:
        f["unit"] = unit
    if values is not None:
        f["values"] = [{"value": v[0], "label_uz": v[1], "label_ru": v[2]} for v in values]
    return f


def I(key, uz, ru, typ="text", required=False, max_len=None):
    d = {"key": key, "label_uz": uz, "label_ru": ru, "type": typ, "required": required}
    if max_len:
        d["max_len"] = max_len
    return d


# ── reusable presets ─────────────────────────────────────────────────────────
COLOR = A("color", "Rang", "Цвет", "color", variant=True)
COND = A("condition", "Holati", "Состояние", "enum", variant=True,
         values=[("new", "Yangi", "Новый"), ("used", "Ishlatilgan", "Б/у")])

SERIAL = I("serial", "Seriya raqami", "Серийный номер", max_len=40)
IMEI1 = I("imei1", "IMEI 1", "IMEI 1", max_len=17)
IMEI2 = I("imei2", "IMEI 2", "IMEI 2", max_len=17)
VIN = I("vin", "Kuzov / VIN raqami", "Кузов / VIN", max_len=20)
ENGINE = I("engine", "Dvigatel raqami", "Номер двигателя", max_len=20)
MAC = I("mac", "MAC manzil", "MAC-адрес", max_len=20)


def storage():
    return A("storage", "Xotira", "Память", "number", variant=True, unit="GB")
def ram():
    return A("ram", "RAM", "ОЗУ", "number", variant=True, unit="GB")
def screen():
    return A("screen", "Ekran o'lchami", "Диагональ", "decimal", unit="dyuym")
def power_w():
    return A("power", "Quvvat", "Мощность", "number", unit="Vt")

SIM2 = [("1sim", "1 SIM", "1 SIM"), ("2sim", "2 SIM", "2 SIM")]
SIM3 = SIM2 + [("esim", "eSIM", "eSIM")]
CPU = [("i3", "Intel i3", "Intel i3"), ("i5", "Intel i5", "Intel i5"),
       ("i7", "Intel i7", "Intel i7"), ("i9", "Intel i9", "Intel i9"),
       ("r5", "Ryzen 5", "Ryzen 5"), ("r7", "Ryzen 7", "Ryzen 7"),
       ("apple", "Apple M", "Apple M"), ("celeron", "Celeron/Pentium", "Celeron/Pentium")]
PANEL = [("ips", "IPS", "IPS"), ("va", "VA", "VA"), ("tn", "TN", "TN"), ("oled", "OLED", "OLED")]
RES = [("hd", "HD", "HD"), ("fhd", "FHD", "FHD"), ("2k", "2K", "2K"), ("4k", "4K", "4K")]
HZ = [("60", "60 Hz", "60 Гц"), ("75", "75 Hz", "75 Гц"), ("144", "144 Hz", "144 Гц"),
      ("165", "165 Hz", "165 Гц"), ("240", "240 Hz", "240 Гц")]
CLOTH = [("xs", "XS", "XS"), ("s", "S", "S"), ("m", "M", "M"), ("l", "L", "L"),
         ("xl", "XL", "XL"), ("xxl", "XXL", "XXL"), ("xxxl", "XXXL", "XXXL")]
GENDER = [("male", "Erkaklar", "Мужской"), ("female", "Ayollar", "Женский"), ("unisex", "Unisex", "Унисекс")]
MAT_FURN = [("wood", "Yog'och", "Дерево"), ("mdf", "MDF/LDSP", "МДФ/ЛДСП"),
            ("metal", "Metall", "Металл"), ("fabric", "Mato", "Ткань"),
            ("leather", "Charm", "Кожа"), ("eco_leather", "Eko charm", "Эко-кожа"),
            ("glass", "Shisha", "Стекло"), ("plastic", "Plastik", "Пластик")]
METAL = [("gold", "Tilla", "Золото"), ("silver", "Kumush", "Серебро"),
         ("white_gold", "Oq tilla", "Белое золото"), ("platinum", "Platina", "Платина"),
         ("steel", "Po'lat", "Сталь")]


def material(values=MAT_FURN, variant=False):
    return A("material", "Material", "Материал", "enum", variant=variant, values=values)


# slug → (attributes, identifiers)
SCHEMAS: dict[str, tuple[list, list]] = {
    # ── 📱 Electronics ──────────────────────────────────────────────────────
    "smartfonlar": ([COLOR, storage(), ram(), screen(),
                     A("sim", "SIM", "SIM", "enum", values=SIM3), COND],
                    [IMEI1, IMEI2, SERIAL]),
    "tugmali-telefonlar": ([COLOR, A("sim", "SIM", "SIM", "enum", values=SIM2), COND],
                           [IMEI1, IMEI2]),
    "planshetlar": ([COLOR, storage(), ram(), screen(),
                     A("connect", "Ulanish", "Связь", "enum",
                       values=[("wifi", "Wi-Fi", "Wi-Fi"), ("cellular", "Wi-Fi + SIM", "Wi-Fi + SIM")]), COND],
                    [SERIAL, IMEI1]),
    "noutbuklar": ([COLOR, A("cpu", "Protsessor", "Процессор", "enum", variant=True, values=CPU),
                    ram(), A("ssd", "SSD", "SSD", "number", variant=True, unit="GB"),
                    screen(), A("gpu", "Videokarta", "Видеокарта", "text"), COND],
                   [SERIAL]),
    "kompyuterlar": ([A("cpu", "Protsessor", "Процессор", "enum", variant=True, values=CPU),
                      ram(), A("ssd", "SSD/HDD", "SSD/HDD", "number", variant=True, unit="GB"),
                      A("gpu", "Videokarta", "Видеокарта", "text"), COND],
                     [SERIAL]),
    "monitorlar": ([A("size_in", "Ekran o'lchami", "Диагональ", "number", variant=True, unit="dyuym"),
                    A("resolution", "Ruxsat", "Разрешение", "enum", values=RES),
                    A("refresh", "Chastota", "Частота", "enum", values=HZ),
                    A("panel", "Panel", "Панель", "enum", values=PANEL), COND],
                   [SERIAL]),
    "printerlar": ([A("ptype", "Turi", "Тип", "enum", variant=True,
                      values=[("inkjet", "Inkjet", "Струйный"), ("laser", "Laser", "Лазерный"),
                              ("mfp", "MFP (3в1)", "МФУ")]),
                    A("colorful", "Rangli", "Цветной", "bool"),
                    A("connect", "Ulanish", "Связь", "enum",
                      values=[("usb", "USB", "USB"), ("wifi", "Wi-Fi", "Wi-Fi")]), COND],
                   [SERIAL]),
    "smart-soatlar": ([COLOR, A("case", "Korpus", "Корпус", "number", variant=True, unit="mm"),
                       A("strap", "Strap", "Ремешок", "enum",
                         values=[("silicone", "Silikon", "Силикон"), ("metal", "Metall", "Металл"),
                                 ("leather", "Charm", "Кожа")]),
                       A("conn", "Aloqa", "Связь", "enum",
                         values=[("gps", "GPS", "GPS"), ("cellular", "GPS + Cellular", "GPS + Cellular")]), COND],
                      [SERIAL]),
    "quloqchinlar": ([COLOR, A("htype", "Turi", "Тип", "enum", variant=True,
                       values=[("tws", "TWS", "TWS"), ("overear", "Over-ear", "Накладные"),
                               ("onear", "On-ear", "На ухо"), ("wired", "Simli", "Проводные")]),
                      A("anc", "Shovqin bostirish (ANC)", "Шумоподавление (ANC)", "bool"), COND],
                     [SERIAL]),
    "kolonkalar": ([COLOR, power_w(),
                    A("connect", "Ulanish", "Связь", "enum",
                      values=[("bt", "Bluetooth", "Bluetooth"), ("wired", "Simli", "Проводная")]), COND],
                   [SERIAL]),
    "powerbanklar": ([A("capacity", "Sig'im", "Ёмкость", "number", variant=True, unit="mAh"),
                      COLOR, power_w()], []),
    "telefon-aksessuarlari": ([A("atype", "Turi", "Тип", "enum", variant=True,
                                values=[("case", "G'ilof", "Чехол"), ("charger", "Zaryadlovchi", "Зарядное"),
                                        ("cable", "Kabel", "Кабель"), ("glass", "Himoya oynasi", "Защитное стекло"),
                                        ("holder", "Tutqich", "Держатель")]),
                               COLOR, A("fit", "Moslik (model)", "Совместимость", "text")], []),
    "kompyuter-aksessuarlari": ([A("atype", "Turi", "Тип", "enum", variant=True,
                                  values=[("keyboard", "Klaviatura", "Клавиатура"), ("mouse", "Sichqoncha", "Мышь"),
                                          ("headset", "Garnitura", "Гарнитура"), ("webcam", "Veb-kamera", "Веб-камера"),
                                          ("pad", "Kovrik", "Коврик")]),
                                 COLOR, A("connect", "Ulanish", "Связь", "enum",
                                          values=[("wired", "Simli", "Проводное"), ("wireless", "Simsiz", "Беспроводное")])], []),
    "o-yin-konsollari": ([A("version", "Versiya", "Версия", "enum", variant=True,
                           values=[("standard", "Standard", "Standard"), ("slim", "Slim", "Slim"),
                                   ("pro", "Pro", "Pro"), ("lite", "Lite", "Lite")]),
                          A("storage", "Xotira", "Память", "number", variant=True, unit="GB"), COLOR, COND],
                         [SERIAL]),
    "router-va-modemlar": ([A("rtype", "Turi", "Тип", "enum", variant=True,
                             values=[("router", "Router", "Роутер"), ("modem", "Modem", "Модем"),
                                     ("mesh", "Mesh", "Mesh")]),
                            A("wifi", "Wi-Fi", "Wi-Fi", "enum",
                              values=[("5", "Wi-Fi 5", "Wi-Fi 5"), ("6", "Wi-Fi 6", "Wi-Fi 6"),
                                      ("6e", "Wi-Fi 6E", "Wi-Fi 6E"), ("7", "Wi-Fi 7", "Wi-Fi 7")]),
                            A("speed", "Tezlik", "Скорость", "text")],
                           [SERIAL, MAC]),

    # ── 🏠 Appliances ───────────────────────────────────────────────────────
    "muzlatgichlar": ([COLOR, A("volume", "Hajmi", "Объём", "number", variant=True, unit="L"),
                       A("ftype", "Turi", "Тип", "enum",
                         values=[("two", "2 kamerali", "Двухкамерный"), ("side", "Side-by-Side", "Side-by-Side"),
                                 ("single", "1 kamerali", "Однокамерный")]),
                       A("nofrost", "No Frost", "No Frost", "bool"), COND], [SERIAL]),
    "kir-yuvish-mashinalari": ([COLOR, A("load", "Yuk", "Загрузка", "number", variant=True, unit="kg"),
                                A("wtype", "Turi", "Тип", "enum",
                                  values=[("front", "Frontal", "Фронтальная"), ("top", "Vertikal", "Вертикальная"),
                                          ("auto", "Avtomat", "Автомат")]), COND], [SERIAL]),
    "gaz-plitalari": ([COLOR, A("burners", "Konfor soni", "Конфорки", "number", variant=True),
                       A("oven", "Duxovka", "Духовка", "enum",
                         values=[("gas", "Gazli", "Газовая"), ("electric", "Elektr", "Электрическая"), ("no", "Yo'q", "Нет")]), COND], [SERIAL]),
    "elektr-plitalar": ([COLOR, A("burners", "Konfor soni", "Конфорки", "number", variant=True),
                         A("surface", "Sirt", "Поверхность", "enum",
                           values=[("induction", "Induksion", "Индукционная"), ("ceramic", "Shisha-keramika", "Стеклокерамика"),
                                   ("disc", "Blin", "Блины")]), COND], [SERIAL]),
    "duxovkalar": ([COLOR, A("volume", "Hajmi", "Объём", "number", variant=True, unit="L"),
                    A("otype", "Turi", "Тип", "enum",
                      values=[("electric", "Elektr", "Электрическая"), ("gas", "Gazli", "Газовая")]), COND], [SERIAL]),
    "mikroto-lqinli-pechlar": ([COLOR, A("volume", "Hajmi", "Объём", "number", variant=True, unit="L"),
                                power_w(), A("grill", "Grill", "Гриль", "bool"), COND], [SERIAL]),
    "changyutgichlar": ([COLOR, power_w(),
                         A("vtype", "Turi", "Тип", "enum", variant=True,
                           values=[("bag", "Sochiqli", "С мешком"), ("cyclone", "Siklon", "Циклон"),
                                   ("robot", "Robot", "Робот"), ("vertical", "Vertikal", "Вертикальный"),
                                   ("wash", "Yuvuvchi", "Моющий")]), COND], [SERIAL]),
    "idish-yuvish-mashinalari": ([COLOR, A("sets", "Komplekt", "Комплектов", "number", variant=True),
                                  A("dwtype", "Turi", "Тип", "enum",
                                    values=[("full", "60 sm", "60 см"), ("narrow", "45 sm", "45 см"),
                                            ("compact", "Kompakt", "Компактная")]), COND], [SERIAL]),
    "konditsionerlar": ([COLOR, A("btu", "Quvvat", "Мощность", "number", variant=True, unit="BTU"),
                         A("inverter", "Invertor", "Инвертор", "bool"), COND], [SERIAL]),
    "isitgichlar": ([COLOR, power_w(),
                     A("htype", "Turi", "Тип", "enum", variant=True,
                       values=[("oil", "Moyli", "Масляный"), ("convector", "Konvektor", "Конвектор"),
                               ("fan", "Ventilyatorli", "Тепловентилятор"), ("infrared", "Infraqizil", "Инфракрасный")]), COND], [SERIAL]),
    "ventilyatorlar": ([COLOR, A("vtype", "Turi", "Тип", "enum", variant=True,
                         values=[("floor", "Pol", "Напольный"), ("table", "Stol", "Настольный"),
                                 ("tower", "Ustun", "Колонный"), ("ceiling", "Shift", "Потолочный")]), power_w()], [SERIAL]),
    "havo-tozalagichlar": ([COLOR, A("area", "Maydon", "Площадь", "number", unit="m²"),
                            A("hepa", "HEPA filtr", "HEPA фильтр", "bool"), COND], [SERIAL]),
    "televizorlar": ([A("size_in", "Ekran o'lchami", "Диагональ", "number", variant=True, unit="dyuym"),
                      A("resolution", "Ruxsat", "Разрешение", "enum", variant=True, values=RES),
                      A("smart", "Smart TV", "Smart TV", "bool"),
                      A("panel", "Panel", "Панель", "enum",
                        values=[("led", "LED", "LED"), ("qled", "QLED", "QLED"), ("oled", "OLED", "OLED")]), COND], [SERIAL]),
    "suv-isitkichlar": ([COLOR, A("volume", "Hajmi", "Объём", "number", variant=True, unit="L"),
                         A("wtype", "Turi", "Тип", "enum",
                           values=[("storage", "To'plovchi", "Накопительный"), ("flow", "Oqim", "Проточный")]), COND], [SERIAL]),
    "oshxona-texnikasi": ([COLOR, A("ktype", "Turi", "Тип", "enum", variant=True,
                            values=[("blender", "Blender", "Блендер"), ("mixer", "Mikser", "Миксер"),
                                    ("kettle", "Choynak", "Чайник"), ("multicooker", "Multivarka", "Мультиварка"),
                                    ("meat_grinder", "Go'sht maydalagich", "Мясорубка"), ("coffee", "Kofe mashina", "Кофемашина"),
                                    ("toaster", "Toster", "Тостер")]), power_w(), COND], [SERIAL]),

    # ── 🛋️ Furniture ────────────────────────────────────────────────────────
    "divanlar": ([COLOR, material(), A("seats", "O'rin soni", "Мест", "number", variant=True),
                  A("mechanism", "Mexanizm", "Механизм", "enum",
                    values=[("none", "Yo'q", "Нет"), ("book", "Kitob", "Книжка"), ("eurobook", "Yevrokitob", "Еврокнижка"),
                            ("dolphin", "Delfin", "Дельфин"), ("accordion", "Akkordeon", "Аккордеон")]), COND], []),
    "krovatlar": ([COLOR, material(),
                   A("bsize", "O'lcham", "Размер", "enum", variant=True,
                     values=[("90", "90×200", "90×200"), ("120", "120×200", "120×200"),
                             ("140", "140×200", "140×200"), ("160", "160×200", "160×200"), ("180", "180×200", "180×200")]),
                   A("storage_box", "Yashik", "Ящик для белья", "bool"), COND], []),
    "shkaflar": ([COLOR, material(), A("doors", "Eshik soni", "Двери", "number", variant=True),
                  A("stype", "Turi", "Тип", "enum",
                    values=[("swing", "Oddiy", "Распашной"), ("sliding", "Kupe", "Купе"), ("corner", "Burchak", "Угловой")]), COND], []),
    "komodlar": ([COLOR, material(), A("drawers", "Tortma soni", "Ящиков", "number", variant=True), COND], []),
    "stol-va-stullar": ([COLOR, material(),
                         A("ttype", "Turi", "Тип", "enum", variant=True,
                           values=[("dining", "Ovqat stoli", "Обеденный"), ("desk", "Yozuv stoli", "Письменный"),
                                   ("coffee", "Jurnal stoli", "Журнальный"), ("chair", "Stul", "Стул"),
                                   ("set", "To'plam", "Комплект")]), COND], []),
    "oshxona-mebellari": ([COLOR, material(),
                           A("length", "Uzunligi", "Длина", "number", variant=True, unit="m"),
                           A("layout", "Shakli", "Форма", "enum",
                             values=[("straight", "To'g'ri", "Прямая"), ("l", "L-shaklida", "Угловая"), ("u", "U-shaklida", "П-образная")]), COND], []),
    "yumshoq-mebellar": ([COLOR, material(),
                          A("ftype", "Turi", "Тип", "enum", variant=True,
                            values=[("sofa", "Divan", "Диван"), ("armchair", "Kreslo", "Кресло"),
                                    ("set", "Yumshoq burchak", "Мягкий уголок"), ("puff", "Puf", "Пуф")]), COND], []),
    "ofis-mebellari": ([COLOR, material(),
                        A("otype", "Turi", "Тип", "enum", variant=True,
                          values=[("desk", "Stol", "Стол"), ("chair", "Kreslo", "Кресло"),
                                  ("cabinet", "Shkaf", "Шкаф"), ("set", "To'plam", "Комплект")]), COND], []),
    "bolalar-mebellari": ([COLOR, material(),
                           A("ktype", "Turi", "Тип", "enum", variant=True,
                             values=[("bed", "Krovat", "Кровать"), ("desk", "Yozuv stoli", "Парта"),
                                     ("wardrobe", "Shkaf", "Шкаф"), ("set", "To'plam", "Комплект")]), COND], []),
    "matraslar": ([A("msize", "O'lcham", "Размер", "enum", variant=True,
                     values=[("90", "90×200", "90×200"), ("120", "120×200", "120×200"),
                             ("140", "140×200", "140×200"), ("160", "160×200", "160×200"), ("180", "180×200", "180×200")]),
                   A("mtype", "Turi", "Тип", "enum",
                     values=[("spring", "Prujinali", "Пружинный"), ("springless", "Prujinasiz", "Беспружинный"),
                             ("orthopedic", "Ortopedik", "Ортопедический")]),
                   A("height", "Balandligi", "Высота", "number", unit="sm")], []),
    "mebel-to-plamlari": ([COLOR, material(),
                           A("room", "Xona", "Комната", "enum", variant=True,
                             values=[("bedroom", "Yotoqxona", "Спальня"), ("living", "Mehmonxona", "Гостиная"),
                                     ("kitchen", "Oshxona", "Кухня"), ("kids", "Bolalar", "Детская")]), COND], []),

    # ── 🚗 Auto ──────────────────────────────────────────────────────────────
    "avtomobillar": ([COLOR, A("year", "Ishlab chiqarilgan yili", "Год выпуска", "number"),
                      A("fuel", "Yoqilg'i", "Топливо", "enum", variant=True,
                        values=[("petrol", "Benzin", "Бензин"), ("diesel", "Dizel", "Дизель"),
                                ("hybrid", "Gibrid", "Гибрид"), ("electric", "Elektr", "Электро"),
                                ("gas", "Gaz/Benzin", "Газ/Бензин")]),
                      A("transmission", "Uzatma", "КПП", "enum", variant=True,
                        values=[("auto", "Avtomat", "Автомат"), ("manual", "Mexanika", "Механика"),
                                ("cvt", "Variator", "Вариатор"), ("robot", "Robot", "Робот")]),
                      A("engine_vol", "Dvigatel hajmi", "Объём двигателя", "decimal", unit="L"),
                      A("mileage", "Yurgan masofa", "Пробег", "number", unit="km"), COND],
                     [VIN, ENGINE]),
    "mototsikllar": ([COLOR, A("engine_cc", "Dvigatel", "Двигатель", "number", variant=True, unit="cc"),
                      A("year", "Yili", "Год", "number"), COND], [VIN, ENGINE]),
    "skuterlar": ([COLOR, A("engine_cc", "Dvigatel", "Двигатель", "number", variant=True, unit="cc"),
                   A("etype", "Turi", "Тип", "enum",
                     values=[("petrol", "Benzin", "Бензин"), ("electric", "Elektr", "Электро")]), COND], [VIN, ENGINE]),
    "elektro-skuterlar": ([COLOR, power_w(),
                           A("range", "Yurish masofasi", "Запас хода", "number", variant=True, unit="km"),
                           A("max_speed", "Tezlik", "Скорость", "number", unit="km/h"), COND], [SERIAL]),
    "velosipedlar": ([COLOR, A("frame", "Rama o'lchami", "Рама", "number", variant=True, unit="dyuym"),
                      A("btype", "Turi", "Тип", "enum", variant=True,
                        values=[("mtb", "MTB", "MTB"), ("road", "Shosse", "Шоссейный"),
                                ("city", "Shahar", "Городской"), ("kids", "Bolalar", "Детский"),
                                ("electric", "Elektr", "Электро")]), COND], [SERIAL]),
    "diskalar": ([A("radius", "Radius", "Радиус", "number", variant=True, unit="R"),
                  A("pcd", "PCD", "PCD", "text"),
                  A("dtype", "Turi", "Тип", "enum",
                    values=[("alloy", "Quyma", "Литые"), ("forged", "Kovani", "Кованые"), ("stamped", "Shtamp", "Штампованные")]), COLOR], []),
    "shinalar": ([A("size", "O'lcham", "Размер", "text", variant=True),
                  A("season", "Mavsum", "Сезон", "enum", variant=True,
                    values=[("summer", "Yozgi", "Летние"), ("winter", "Qishki", "Зимние"), ("allseason", "Har faslli", "Всесезонные")])], []),
    "akkumulyatorlar": ([A("capacity", "Sig'im", "Ёмкость", "number", variant=True, unit="Ah"),
                         A("polarity", "Polyarlik", "Полярность", "enum",
                           values=[("l", "Chap (L+)", "Прямая (L+)"), ("r", "O'ng (R+)", "Обратная (R+)")]), COND], []),
    "avto-audio": ([COLOR, A("atype", "Turi", "Тип", "enum", variant=True,
                     values=[("headunit", "Magnitola", "Магнитола"), ("speakers", "Dinamiklar", "Динамики"),
                             ("sub", "Sabvufer", "Сабвуфер"), ("amp", "Usilitel", "Усилитель")]), power_w()], [SERIAL]),
    "avto-aksessuarlar": ([A("atype", "Turi", "Тип", "enum", variant=True,
                            values=[("dvr", "Videoregistrator", "Видеорегистратор"), ("nav", "Navigator", "Навигатор"),
                                    ("cover", "Chexol", "Чехлы"), ("mat", "Polik", "Коврики"),
                                    ("rack", "Bagajnik", "Багажник"), ("care", "Parvarish", "Уход")]), COLOR], []),
    "ehtiyot-qismlar": ([A("ptype", "Turi", "Тип", "enum", variant=True,
                          values=[("engine", "Dvigatel", "Двигатель"), ("brakes", "Tormoz", "Тормоза"),
                                  ("suspension", "Xodovoy", "Подвеска"), ("filter", "Filtr", "Фильтры"),
                                  ("electric", "Elektr", "Электрика"), ("body", "Kuzov", "Кузов")]),
                         A("oem", "OEM raqami", "OEM номер", "text"),
                         A("car_model", "Avtomobil modeli", "Модель авто", "text"), COND], []),
    "moy-va-texnik-suyuqliklar": ([A("ftype", "Turi", "Тип", "enum", variant=True,
                                    values=[("engine_oil", "Motor moyi", "Моторное масло"), ("gear_oil", "Transmissiya moyi", "Трансмиссионное"),
                                            ("antifreeze", "Antifriz", "Антифриз"), ("brake", "Tormoz suyuqligi", "Тормозная жидкость")]),
                                   A("viscosity", "Viskozitet", "Вязкость", "text"),
                                   A("volume", "Hajmi", "Объём", "decimal", variant=True, unit="L")], []),

    # ── 👗 Clothing ──────────────────────────────────────────────────────────
    "erkaklar-kiyimi": ([COLOR, A("size", "O'lcham", "Размер", "enum", variant=True, values=CLOTH),
                         material([("cotton", "Paxta", "Хлопок"), ("wool", "Jun", "Шерсть"),
                                   ("denim", "Jinsi", "Джинса"), ("leather", "Charm", "Кожа"),
                                   ("synthetic", "Sintetika", "Синтетика")])], []),
    "ayollar-kiyimi": ([COLOR, A("size", "O'lcham", "Размер", "enum", variant=True, values=CLOTH),
                        material([("cotton", "Paxta", "Хлопок"), ("silk", "Ipak", "Шёлк"),
                                  ("wool", "Jun", "Шерсть"), ("synthetic", "Sintetika", "Синтетика")])], []),
    "bolalar-kiyimi": ([COLOR, A("age", "Yosh", "Возраст", "text", variant=True),
                        A("size", "O'lcham (bo'y)", "Размер (рост)", "number", variant=True, unit="sm")], []),
    "erkaklar-oyoq-kiyimi": ([COLOR, A("size", "O'lcham", "Размер", "number", variant=True, unit="EU"),
                              material([("leather", "Charm", "Кожа"), ("textile", "Mato", "Текстиль"),
                                        ("eco", "Eko charm", "Эко-кожа"), ("rubber", "Rezina", "Резина")])], []),
    "ayollar-oyoq-kiyimi": ([COLOR, A("size", "O'lcham", "Размер", "number", variant=True, unit="EU"),
                             A("heel", "Poshna", "Каблук", "number", unit="sm"),
                             material([("leather", "Charm", "Кожа"), ("textile", "Mato", "Текстиль"), ("eco", "Eko charm", "Эко-кожа")])], []),
    "sport-kiyimlari": ([COLOR, A("size", "O'lcham", "Размер", "enum", variant=True, values=CLOTH),
                         A("gender", "Jins", "Пол", "enum", values=GENDER)], []),
    "sumkalar": ([COLOR, material([("leather", "Charm", "Кожа"), ("eco", "Eko charm", "Эко-кожа"),
                                   ("textile", "Mato", "Текстиль")]),
                  A("btype", "Turi", "Тип", "enum", variant=True,
                    values=[("hand", "Qo'l sumka", "Сумка"), ("backpack", "Ryukzak", "Рюкзак"),
                            ("clutch", "Klatch", "Клатч"), ("travel", "Sayohat", "Дорожная")])], []),
    "soatlar": ([COLOR, A("mech", "Mexanizm", "Механизм", "enum", variant=True,
                  values=[("quartz", "Kvarts", "Кварцевые"), ("mechanical", "Mexanik", "Механические"),
                          ("smart", "Smart", "Смарт")]),
                 A("strap", "Strap", "Ремешок", "enum",
                   values=[("metal", "Metall", "Металл"), ("leather", "Charm", "Кожа"), ("silicone", "Silikon", "Силикон")]), COND], [SERIAL]),
    "aksessuarlar": ([COLOR, A("atype", "Turi", "Тип", "enum", variant=True,
                       values=[("cap", "Bosh kiyim", "Головной убор"), ("belt", "Kamar", "Ремень"),
                               ("scarf", "Sharf", "Шарф"), ("glasses", "Ko'zoynak", "Очки"),
                               ("wallet", "Hamyon", "Кошелёк")])], []),

    # ── 💍 Jewelry ───────────────────────────────────────────────────────────
    "uzuklar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                 A("sample", "Proba", "Проба", "enum", values=[("585", "585", "585"), ("750", "750", "750"), ("925", "925", "925")]),
                 A("ring_size", "O'lcham", "Размер", "number", variant=True),
                 A("stone", "Tosh", "Камень", "text"),
                 A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "ziraklar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                  A("sample", "Proba", "Проба", "enum", values=[("585", "585", "585"), ("750", "750", "750"), ("925", "925", "925")]),
                  A("stone", "Tosh", "Камень", "text"),
                  A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "brasletlar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                    A("length", "Uzunligi", "Длина", "decimal", variant=True, unit="sm"),
                    A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "zanjirlar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                   A("length", "Uzunligi", "Длина", "decimal", variant=True, unit="sm"),
                   A("weave", "To'qima", "Плетение", "text"),
                   A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "kulonlar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                  A("stone", "Tosh", "Камень", "text"),
                  A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "komplektlar": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                     A("pieces", "Buyumlar soni", "Предметов", "number"),
                     A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "erkaklar-zargarligi": ([A("metal", "Metall", "Металл", "enum", variant=True, values=METAL),
                             A("jtype", "Turi", "Тип", "enum", variant=True,
                               values=[("ring", "Uzuk", "Кольцо"), ("chain", "Zanjir", "Цепь"),
                                       ("bracelet", "Braslet", "Браслет"), ("cufflinks", "Zaponka", "Запонки")]),
                             A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "kumush-buyumlar": ([A("sample", "Proba", "Проба", "enum", values=[("925", "925", "925"), ("875", "875", "875")]),
                         A("jtype", "Turi", "Тип", "enum", variant=True,
                           values=[("ring", "Uzuk", "Кольцо"), ("earrings", "Zirak", "Серьги"),
                                   ("set", "Komplekt", "Набор"), ("tableware", "Stol buyumlari", "Столовое серебро")]),
                         A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),
    "tilla-buyumlar": ([A("sample", "Proba", "Проба", "enum", variant=True,
                          values=[("585", "585", "585"), ("750", "750", "750"), ("999", "999", "999")]),
                        A("jtype", "Turi", "Тип", "enum", variant=True,
                          values=[("ring", "Uzuk", "Кольцо"), ("earrings", "Zirak", "Серьги"),
                                  ("chain", "Zanjir", "Цепь"), ("bar", "Quyma", "Слиток")]),
                        A("weight", "Og'irligi", "Вес", "decimal", unit="g")], []),

    # ── 🏗️ Construction ──────────────────────────────────────────────────────
    "sement": ([A("grade", "Marka", "Марка", "enum", variant=True,
                 values=[("m400", "M400", "M400"), ("m500", "M500", "M500"), ("pc", "PC", "ПЦ")]),
                A("weight", "Qadoq", "Фасовка", "number", variant=True, unit="kg")], []),
    "g-isht": ([A("btype", "Turi", "Тип", "enum", variant=True,
                 values=[("red", "Qizil", "Красный"), ("silicate", "Silikat", "Силикатный"),
                         ("facing", "Bezak", "Облицовочный"), ("fireproof", "O't bardosh", "Огнеупорный")]),
                A("size", "O'lcham", "Размер", "text")], []),
    "armatura": ([A("diameter", "Diametr", "Диаметр", "number", variant=True, unit="mm"),
                  A("grade", "Klass", "Класс", "text"),
                  A("length", "Uzunligi", "Длина", "number", unit="m")], []),
    "kafel": ([COLOR, A("size", "O'lcham", "Размер", "text", variant=True),
               A("surface", "Sirt", "Поверхность", "enum",
                 values=[("matte", "Mat", "Матовая"), ("glossy", "Yaltiroq", "Глянцевая")]),
               A("usage", "Joylashuv", "Применение", "enum",
                 values=[("floor", "Pol", "Пол"), ("wall", "Devor", "Стена")])], []),
    "laminat": ([COLOR, A("class", "Klass", "Класс", "enum", variant=True,
                  values=[("31", "31", "31"), ("32", "32", "32"), ("33", "33", "33"), ("34", "34", "34")]),
                 A("thickness", "Qalinligi", "Толщина", "number", unit="mm")], []),
    "linoleum": ([COLOR, A("width", "Eni", "Ширина", "decimal", variant=True, unit="m"),
                  A("ltype", "Turi", "Тип", "enum",
                    values=[("household", "Maishiy", "Бытовой"), ("commercial", "Tijorat", "Коммерческий")])], []),
    "bo-yoqlar": ([COLOR, A("volume", "Hajmi", "Объём", "decimal", variant=True, unit="L"),
                   A("base", "Asosi", "Основа", "enum",
                     values=[("water", "Suvli", "Водная"), ("oil", "Moyli", "Масляная"), ("acrylic", "Akril", "Акриловая")])], []),
    "santexnika": ([COLOR, A("stype", "Turi", "Тип", "enum", variant=True,
                     values=[("faucet", "Smesitel", "Смеситель"), ("sink", "Rakovina", "Раковина"),
                             ("toilet", "Unitaz", "Унитаз"), ("bath", "Vanna", "Ванна"),
                             ("shower", "Dush", "Душ")])], []),
    "elektr-jihozlari": ([A("etype", "Turi", "Тип", "enum", variant=True,
                           values=[("cable", "Kabel", "Кабель"), ("socket", "Rozetka", "Розетка"),
                                   ("breaker", "Avtomat", "Автомат"), ("panel", "Shchit", "Щит"),
                                   ("switch", "Vyklyuchatel", "Выключатель")])], []),
    "eshiklar": ([COLOR, A("dtype", "Turi", "Тип", "enum", variant=True,
                   values=[("interior", "Ichki", "Межкомнатная"), ("entrance", "Kirish", "Входная"),
                           ("metal", "Temir", "Металлическая")]),
                  material()], []),
    "derazalar": ([COLOR, A("chambers", "Kamera soni", "Камеры", "number", variant=True),
                   A("mtype", "Material", "Материал", "enum",
                     values=[("pvc", "PVX", "ПВХ"), ("aluminum", "Alyumin", "Алюминий"), ("wood", "Yog'och", "Дерево")])], []),
    "tom-yopish-materiallari": ([COLOR, A("rtype", "Turi", "Тип", "enum", variant=True,
                                  values=[("metal_tile", "Metalcherepitsa", "Металлочерепица"),
                                          ("profnastil", "Profnastil", "Профнастил"),
                                          ("soft", "Yumshoq tom", "Мягкая кровля"),
                                          ("slate", "Shifer", "Шифер")])], []),
    "qurilish-asboblari": ([A("ttype", "Turi", "Тип", "enum", variant=True,
                             values=[("drill", "Drel/Shurupovert", "Дрель/Шуруповёрт"), ("grinder", "Bolgarka", "Болгарка"),
                                     ("saw", "Arra", "Пила"), ("hammer", "Perforator", "Перфоратор"),
                                     ("welder", "Payvand", "Сварочный"), ("hand", "Qo'l asbob", "Ручной инструмент")]),
                            A("powered", "Quvvat manbai", "Питание", "enum",
                              values=[("network", "Tarmoq", "Сеть"), ("battery", "Akkumulyator", "Аккумулятор")]), COND], [SERIAL]),

    # ── 🏡 Home goods ────────────────────────────────────────────────────────
    "gilamlar": ([COLOR, A("size", "O'lcham", "Размер", "text", variant=True),
                  material([("wool", "Jun", "Шерсть"), ("acrylic", "Akril", "Акрил"),
                            ("synthetic", "Sintetika", "Синтетика"), ("silk", "Ipak", "Шёлк")])], []),
    "pardalar": ([COLOR, A("ptype", "Turi", "Тип", "enum", variant=True,
                   values=[("tulle", "Tyul", "Тюль"), ("blackout", "Blekaut", "Блэкаут"),
                           ("roller", "Rulonli", "Рулонные"), ("classic", "Klassik", "Классические")]),
                  A("width", "Eni", "Ширина", "number", unit="sm")], []),
    "lyustralar": ([COLOR, A("ltype", "Turi", "Тип", "enum", variant=True,
                     values=[("chandelier", "Lyustra", "Люстра"), ("spot", "Spot", "Спот"),
                             ("led", "LED panel", "LED панель"), ("wall", "Bra", "Бра"),
                             ("floor", "Torsher", "Торшер")]),
                    A("lamps", "Lampa soni", "Ламп", "number", variant=True)], []),
    "uy-dekorlari": ([COLOR, A("dtype", "Turi", "Тип", "enum", variant=True,
                       values=[("vase", "Vaza", "Ваза"), ("frame", "Ramka", "Рамка"),
                               ("mirror", "Oyna", "Зеркало"), ("candle", "Sham", "Свеча"),
                               ("statue", "Haykalcha", "Статуэтка")]), material()], []),
    "oshxona-buyumlari": ([COLOR, A("ktype", "Turi", "Тип", "enum", variant=True,
                            values=[("cookware", "Idishlar to'plami", "Набор посуды"), ("pan", "Tova", "Сковорода"),
                                    ("pot", "Qozon", "Кастрюля"), ("dishes", "Servis", "Сервиз"),
                                    ("cutlery", "Pichoq/qoshiq", "Столовые приборы")]),
                           material([("steel", "Po'lat", "Сталь"), ("aluminum", "Alyumin", "Алюминий"),
                                     ("ceramic", "Keramika", "Керамика"), ("glass", "Shisha", "Стекло"),
                                     ("teflon", "Teflon", "Тефлон")])], []),
    "yotoqxona-buyumlari": ([COLOR, A("size", "O'lcham", "Размер", "enum", variant=True,
                              values=[("single", "1 kishilik", "1.5-спальный"), ("double", "2 kishilik", "2-спальный"),
                                      ("euro", "Yevro", "Евро"), ("family", "Oilaviy", "Семейный")]),
                             material([("cotton", "Paxta", "Хлопок"), ("satin", "Satin", "Сатин"),
                                       ("silk", "Ipak", "Шёлк"), ("bamboo", "Bambuk", "Бамбук")])], []),
    "hammom-jihozlari": ([COLOR, A("htype", "Turi", "Тип", "enum", variant=True,
                           values=[("towel", "Sochiq", "Полотенце"), ("set", "To'plam", "Набор"),
                                   ("accessory", "Aksessuar", "Аксессуар"), ("filter", "Suv filtri", "Фильтр для воды")])], []),
    "ko-rpa-to-shaklar": ([COLOR, A("btype", "Turi", "Тип", "enum", variant=True,
                            values=[("blanket", "Ko'rpa", "Одеяло"), ("pillow", "Yostiq", "Подушка"),
                                    ("set", "To'plam", "Комплект"), ("topper", "Topper", "Топпер")]),
                           A("filler", "To'ldiruvchi", "Наполнитель", "text")], []),
    "uy-tekstili": ([COLOR, A("ttype", "Turi", "Тип", "enum", variant=True,
                      values=[("tablecloth", "Dasturxon", "Скатерть"), ("towel", "Sochiq", "Полотенце"),
                              ("napkin", "Salfetka", "Салфетка"), ("plaid", "Pled", "Плед")]),
                     material([("cotton", "Paxta", "Хлопок"), ("linen", "Zig'ir", "Лён"), ("synthetic", "Sintetika", "Синтетика")])], []),

    # ── ☀️ Solar ──────────────────────────────────────────────────────────────
    "quyosh-panellari": ([power_w(),
                          A("ptype", "Turi", "Тип", "enum", variant=True,
                            values=[("mono", "Monokristall", "Монокристалл"), ("poly", "Polikristall", "Поликристалл"),
                                    ("bifacial", "Bifacial", "Двусторонняя")]),
                          A("voltage", "Kuchlanish", "Напряжение", "number", unit="V"), COND], [SERIAL]),
    "invertorlar": ([power_w(),
                     A("itype", "Turi", "Тип", "enum", variant=True,
                       values=[("on", "On-grid", "Сетевой"), ("off", "Off-grid", "Автономный"),
                               ("hybrid", "Gibrid", "Гибридный")]),
                     A("phase", "Faza", "Фаза", "enum",
                       values=[("1", "1 fazali", "1-фазный"), ("3", "3 fazali", "3-фазный")]), COND], [SERIAL]),
    "akkumulyatorlar-solar": ([A("capacity", "Sig'im", "Ёмкость", "number", variant=True, unit="Ah"),
                               A("voltage", "Kuchlanish", "Напряжение", "number", variant=True, unit="V"),
                               A("chem", "Turi", "Тип", "enum",
                                 values=[("lifepo4", "LiFePO4", "LiFePO4"), ("liion", "Li-ion", "Li-ion"),
                                         ("gel", "Gel", "Gel"), ("agm", "AGM", "AGM")]), COND], [SERIAL]),
    "solar-lampalar": ([COLOR, power_w(),
                        A("ltype", "Turi", "Тип", "enum", variant=True,
                          values=[("street", "Ko'cha", "Уличный"), ("garden", "Bog'", "Садовый"),
                                  ("projector", "Projektor", "Прожектор")])], []),
    "solar-komplektlar": ([power_w(),
                           A("ktype", "Turi", "Тип", "enum", variant=True,
                             values=[("home", "Uy uchun", "Для дома"), ("pump", "Nasos uchun", "Для насоса"),
                                     ("commercial", "Tijorat", "Коммерческий")]), COND], [SERIAL]),
    "zaryad-stansiyalari": ([A("capacity", "Sig'im", "Ёмкость", "number", variant=True, unit="Wh"),
                             power_w(), COLOR, COND], [SERIAL]),

    # ── 📹 Security ────────────────────────────────────────────────────────────
    "videokameralar": ([A("resolution", "Ruxsat", "Разрешение", "enum", variant=True,
                          values=[("2mp", "2 MP", "2 МП"), ("4mp", "4 MP", "4 МП"),
                                  ("5mp", "5 MP", "5 МП"), ("8mp", "8 MP (4K)", "8 МП (4K)")]),
                         A("ctype", "Turi", "Тип", "enum", variant=True,
                           values=[("dome", "Dome", "Купольная"), ("bullet", "Bullet", "Цилиндрическая"),
                                   ("ptz", "PTZ", "PTZ"), ("wifi", "Wi-Fi", "Wi-Fi")]),
                         A("conn", "Ulanish", "Подключение", "enum",
                           values=[("ip", "IP", "IP"), ("analog", "Analog (HD)", "Аналог (HD)")]), COND], [SERIAL, MAC]),
    "dvr-nvr-qurilmalar": ([A("channels", "Kanallar soni", "Каналы", "number", variant=True),
                            A("dtype", "Turi", "Тип", "enum", variant=True,
                              values=[("nvr", "NVR (IP)", "NVR (IP)"), ("dvr", "DVR (analog)", "DVR (аналог)")]), COND], [SERIAL]),
    "domofonlar": ([COLOR, A("dtype", "Turi", "Тип", "enum", variant=True,
                     values=[("video", "Video", "Видео"), ("audio", "Audio", "Аудио"),
                             ("ip", "IP", "IP")]),
                    screen(), COND], [SERIAL]),
    "signalizatsiyalar": ([A("stype", "Turi", "Тип", "enum", variant=True,
                            values=[("wireless", "Simsiz", "Беспроводная"), ("wired", "Simli", "Проводная"),
                                    ("gsm", "GSM", "GSM")]), COND], [SERIAL]),
    "smart-qulf-tizimlari": ([COLOR, A("unlock", "Ochish usuli", "Разблокировка", "enum", variant=True,
                               values=[("fingerprint", "Barmoq izi", "Отпечаток"), ("code", "Kod", "Код"),
                                       ("card", "Karta", "Карта"), ("face", "Yuz", "Лицо"), ("app", "Ilova", "Приложение")]), COND], [SERIAL]),
    "kirishni-nazorat-qilish-tizimlari": ([A("dtype", "Turi", "Тип", "enum", variant=True,
                                            values=[("turnstile", "Turniket", "Турникет"), ("controller", "Kontroller", "Контроллер"),
                                                    ("reader", "Karta o'qigich", "Считыватель"), ("biometric", "Biometrik", "Биометрия")]), COND], [SERIAL]),

    # ── 🧸 Kids ──────────────────────────────────────────────────────────────
    "aravachalar": ([COLOR, A("atype", "Turi", "Тип", "enum", variant=True,
                      values=[("2in1", "2в1", "2в1"), ("3in1", "3в1", "3в1"),
                              ("walking", "Progulochnaya", "Прогулочная"), ("light", "Yengil", "Лёгкая")]), COND], []),
    "velosipedlar-kids": ([COLOR, A("wheel", "G'ildirak", "Колесо", "number", variant=True, unit="dyuym"),
                           A("age", "Yosh", "Возраст", "text"), COND], []),
    "elektromobillar": ([COLOR, A("seats", "O'rin", "Мест", "number", variant=True),
                         A("battery", "Akkumulyator", "Аккумулятор", "text"), COND], []),
    "karovatlar": ([COLOR, material(),
                    A("ktype", "Turi", "Тип", "enum", variant=True,
                      values=[("crib", "Beshik", "Люлька"), ("standard", "Standart", "Стандарт"),
                              ("transformer", "Transformer", "Трансформер")]), COND], []),
    "o-yinchoqlar": ([COLOR, A("age", "Yosh", "Возраст", "text", variant=True),
                      A("ttype", "Turi", "Тип", "enum",
                        values=[("constructor", "Konstruktor", "Конструктор"), ("doll", "Qo'g'irchoq", "Кукла"),
                                ("car", "Mashina", "Машинка"), ("soft", "Yumshoq", "Мягкая"),
                                ("educational", "Rivojlantiruvchi", "Развивающая")])], []),
    "maktab-buyumlari": ([A("stype", "Turi", "Тип", "enum", variant=True,
                           values=[("backpack", "Ryukzak", "Рюкзак"), ("notebook", "Daftar", "Тетрадь"),
                                   ("pen", "Ruchka/qalam", "Ручка/карандаш"), ("set", "To'plam", "Набор"),
                                   ("art", "Chizish", "Для рисования")]), COLOR], []),
    "bolalar-mebellari-kids": ([COLOR, material(),
                                A("ktype", "Turi", "Тип", "enum", variant=True,
                                  values=[("bed", "Krovat", "Кровать"), ("desk", "Parta", "Парта"),
                                          ("wardrobe", "Shkaf", "Шкаф"), ("set", "To'plam", "Комплект")]), COND], []),
    "chaqaloq-mahsulotlari": ([A("ctype", "Turi", "Тип", "enum", variant=True,
                                values=[("bottle", "Buticha", "Бутылочка"), ("pacifier", "Soska", "Соска"),
                                        ("pump", "Sut nasosi", "Молокоотсос"), ("care", "Parvarish", "Уход"),
                                        ("feeding", "Ovqatlantirish", "Кормление")]), COLOR], []),

    # ── 📦 Other ──────────────────────────────────────────────────────────────
    "boshqa-mahsulotlar": ([COLOR, COND], [SERIAL]),
}


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "UPDATE catalog_categories SET attribute_schema = CAST(:a AS json), "
        "identifier_schema = CAST(:i AS json) WHERE slug = :slug"
    )
    for slug, (attrs, ids) in SCHEMAS.items():
        conn.execute(stmt, {"slug": slug, "a": json.dumps(attrs), "i": json.dumps(ids)})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE catalog_categories SET attribute_schema = '[]'::json, identifier_schema = '[]'::json"
    ))
