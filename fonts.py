#!/usr/bin/env python
r"""Локальные шрифты для сайтов Васи: скачивает Google Fonts в проект, чтобы браузер
посетителя не ходил на fonts.googleapis.com / fonts.gstatic.com (152-ФЗ: IP-адрес
посетителя не уходит за рубеж без согласия).

    python fonts.py <slug> "Manrope:wght@600;700" "Inter:wght@400;500"

Результат:
    сайты/<slug>/site/assets/fonts/*.woff2    файлы (только latin + cyrillic + cyrillic-ext)
    сайты/<slug>/site/assets/fonts/fonts.css  @font-face с относительными путями

В index.html вместо ссылки на Google:
    <link rel="stylesheet" href="assets/fonts/fonts.css">
и preload для двух главных файлов (заголовок 700, текст 400).
"""
import os, re, sys

import requests

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")   # с таким UA Google отдаёт woff2
KEEP = {"latin", "cyrillic", "cyrillic-ext"}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    slug, families = sys.argv[1], sys.argv[2:]
    out = os.path.join(HERE, "сайты", slug, "site", "assets", "fonts")
    os.makedirs(out, exist_ok=True)
    css_all = []
    for fam in families:
        url = "https://fonts.googleapis.com/css2?family=" + fam.replace(" ", "+") + "&display=swap"
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code != 200:
            sys.exit(f"Google Fonts не отдал {fam}: {r.status_code}")
        blocks = re.findall(r"/\* (\S+) \*/\s*(@font-face \{.*?\})", r.text, re.S)
        if not blocks:
            sys.exit(f"Не разобрал CSS для {fam}")
        n = 0
        for subset, block in blocks:
            if subset not in KEEP:
                continue
            m = re.search(r"font-family: '([^']+)';.*?font-weight: (\d+);.*?url\((https://[^)]+\.woff2)\)", block, re.S)
            if not m:
                continue
            name, weight, src = m.groups()
            style = "italic" if "font-style: italic" in block else "normal"
            fname = f"{name.lower().replace(' ', '-')}-{weight}{'-italic' if style == 'italic' else ''}-{subset}.woff2"
            data = requests.get(src, headers={"User-Agent": UA}, timeout=60).content
            with open(os.path.join(out, fname), "wb") as f:
                f.write(data)
            css_all.append(block.replace(src, fname))
            n += 1
        print(f"  {fam}: {n} файлов")
    with open(os.path.join(out, "fonts.css"), "w", encoding="utf-8") as f:
        f.write("/* Шрифты лежат локально: сайт не обращается к серверам Google */\n" + "\n".join(css_all) + "\n")
    total = sum(os.path.getsize(os.path.join(out, x)) for x in os.listdir(out))
    print(f"Готово: {out}\\fonts.css, всего {total/1024:.0f} КБ")
    print('В <head>: <link rel="stylesheet" href="assets/fonts/fonts.css">')


if __name__ == "__main__":
    main()
