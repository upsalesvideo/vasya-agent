#!/usr/bin/env python
r"""Заливка сайта Васи на хостинг по SFTP: https://<домен>/<slug>/

    python deploy.py <slug>              залить сайты/<slug>/site/ целиком
    python deploy.py <slug> --check      только проверить, что сайт отвечает 200
    python deploy.py <slug> --remove     снять сайт с хостинга (папка удаляется целиком)
    python deploy.py --list              какие сайты Васи уже лежат на хостинге

Настройки хостинга — файл hosting.json рядом со скриптом (в .gitignore) или ~/.secrets/beget.json:
  {
    "host": "server.beget.com",              SFTP-хост (порт 22)
    "user": "login",
    "password": "…",
    "remote_root": "/home/u/login/site.ru/public_html",   папка сайта на хостинге
    "public_url": "https://site.ru"          адрес, по которому сайт открывается
  }
Или переменные окружения BEGET_HOST / BEGET_USER / BEGET_PASS / BEGET_ROOT / BEGET_URL.
Подходит для Beget, Timeweb, reg.ru и любого хостинга с SFTP-доступом.

Что делает:
  1. Рекурсивно кладёт все файлы из сайты/<slug>/site/ в <remote_root>/<slug>/
     (кроме служебного: __pycache__, .git, *.pyc, db.sqlite3, .env, проверка/).
  2. Кладёт рядом .htaccess (отключает WordPress-правила родителя, кеш, gzip).
  3. Удаляет на хостинге файлы, которых больше нет локально (чистая перезаливка).
  4. Проверяет по HTTP, что <public_url>/<slug>/ отвечает 200
     и что в ответе есть <title> из локального index.html.

Трогает только папки со своей меткой .vasya: чужие папки (WordPress, другие сайты) не тронет.
Только для статических сайтов. Магазин на Flask сюда не льётся (см. SKILL.md).
"""
import json, os, posixpath, re, sys

import paramiko
import requests

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILES = [os.path.join(HERE, "hosting.json"), os.path.expanduser("~/.secrets/beget.json")]
MARKER = ".vasya"   # файл-метка: папку с ней создал Вася, только такие можно удалять/перезаливать
RESERVED = {"wp-admin", "wp-content", "wp-includes", "cgi-bin", "bg", "wb", "vasya"}
SITES = os.path.join(HERE, "сайты")


def config():
    c = {}
    for p in CONFIG_FILES:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                c = json.load(f)
            break
    env = {"host": "BEGET_HOST", "user": "BEGET_USER", "password": "BEGET_PASS",
           "remote_root": "BEGET_ROOT", "public_url": "BEGET_URL"}
    for k, v in env.items():
        if os.environ.get(v):
            c[k] = os.environ[v]
    missing = [k for k in ("host", "user", "password", "remote_root", "public_url") if not c.get(k)]
    if missing:
        sys.exit(f"Нет доступов к хостингу ({', '.join(missing)}): создай {CONFIG_FILES[0]} "
                 "по образцу из шапки deploy.py или задай BEGET_HOST/BEGET_USER/BEGET_PASS.")
    c["public_url"] = c["public_url"].rstrip("/")
    return c


CFG = config()
REMOTE_ROOT = CFG["remote_root"]
PUBLIC_URL = CFG["public_url"]
SKIP_DIRS = {"__pycache__", ".git", "проверка", "node_modules"}
SKIP_FILES = {"db.sqlite3", ".env", ".DS_Store", "Thumbs.db"}
SKIP_EXT = {".pyc", ".py", ".md"}

HTACCESS = """# Сайт, собранный Васей: WordPress-правила родителя здесь не нужны
<IfModule mod_rewrite.c>
  RewriteEngine Off
</IfModule>

DirectoryIndex index.html

<IfModule mod_deflate.c>
  AddOutputFilterByType DEFLATE text/html text/css application/javascript image/svg+xml
</IfModule>

<IfModule mod_expires.c>
  ExpiresActive On
  ExpiresByType text/css              "access plus 7 days"
  ExpiresByType application/javascript "access plus 7 days"
  ExpiresByType image/webp            "access plus 30 days"
  ExpiresByType image/jpeg            "access plus 30 days"
  ExpiresByType image/png             "access plus 30 days"
  ExpiresByType image/svg+xml         "access plus 30 days"
  ExpiresByType text/html             "access plus 10 minutes"
</IfModule>

<IfModule mod_headers.c>
  Header set X-Content-Type-Options "nosniff"
  Header set Referrer-Policy "strict-origin-when-cross-origin"
</IfModule>
"""


def connect():
    t = paramiko.Transport((CFG["host"], int(CFG.get("port", 22))))
    t.connect(username=CFG["user"], password=CFG["password"])
    return t, paramiko.SFTPClient.from_transport(t)


def ensure_dir(sf, path):
    try:
        sf.stat(path)
    except IOError:
        ensure_dir(sf, posixpath.dirname(path))
        sf.mkdir(path)
        sf.chmod(path, 0o755)


def local_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or os.path.splitext(name)[1] in SKIP_EXT:
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, root).replace(os.sep, "/"), full


def remote_files(sf, root, prefix=""):
    out = []
    for attr in sf.listdir_attr(posixpath.join(root, prefix) if prefix else root):
        rel = posixpath.join(prefix, attr.filename) if prefix else attr.filename
        if attr.st_mode is not None and (attr.st_mode & 0o40000):
            out.extend(remote_files(sf, root, rel))
        else:
            out.append(rel)
    return out


def check(slug, title=None):
    url = f"{PUBLIC_URL}/{slug}/"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 vasya-deploy", "Cache-Control": "no-cache"})
    except requests.RequestException as e:
        print(f"ОШИБКА: {url} недоступен: {e}")
        return False
    code, body = r.status_code, r.content.decode("utf-8", "replace")
    ok = code == 200
    if title:
        ok = ok and title in body
        print(f"{url} → {code}, заголовок {'совпал' if title in body else 'НЕ совпал'}: {title!r}")
    else:
        print(f"{url} → {code}")
    return ok


def deploy(slug):
    local_root = os.path.join(SITES, slug, "site")
    if not os.path.isdir(local_root):
        sys.exit(f"Нет папки {local_root}")
    if os.path.exists(os.path.join(local_root, "app.py")):
        sys.exit("Это магазин на Flask, а не статический сайт. На Beget через deploy.py он не заливается — см. SKILL.md, «Выкладка».")
    index = os.path.join(local_root, "index.html")
    if not os.path.exists(index):
        sys.exit("Нет index.html в site/")
    with open(index, encoding="utf-8") as f:
        m = re.search(r"<title>(.*?)</title>", f.read(), re.S | re.I)
    title = m.group(1).strip() if m else None

    if slug in RESERVED:
        sys.exit(f"Папка /{slug}/ занята (WordPress или другой проект). Возьми другой slug.")
    remote_root = posixpath.join(REMOTE_ROOT, slug)
    t, sf = connect()
    try:
        if not is_vasya_dir(sf, remote_root, allow_missing=True):
            sys.exit(f"На хостинге уже есть /{slug}/, и это не сайт Васи. Перезаливать не буду — возьми другой slug.")
        ensure_dir(sf, remote_root)
        with sf.open(posixpath.join(remote_root, MARKER), "w") as f:
            f.write("создано deploy.py агента Васи\n")
        wanted = {MARKER: 1}
        total = 0
        for rel, full in local_files(local_root):
            remote = posixpath.join(remote_root, rel)
            ensure_dir(sf, posixpath.dirname(remote))
            sf.put(full, remote)
            sf.chmod(remote, 0o644)
            size = os.path.getsize(full)
            total += size
            wanted[rel] = size
            print(f"  {rel:48} {size:>9} б")
        with sf.open(posixpath.join(remote_root, ".htaccess"), "w") as f:
            f.write(HTACCESS)
        wanted[".htaccess"] = len(HTACCESS)

        stale = [r for r in remote_files(sf, remote_root) if r not in wanted]
        for rel in stale:
            sf.remove(posixpath.join(remote_root, rel))
            print(f"  удалён устаревший: {rel}")
    finally:
        sf.close(); t.close()

    gaps = 0
    for rel in wanted:
        if rel.endswith(".html"):
            with open(os.path.join(local_root, rel.replace("/", os.sep)), encoding="utf-8", errors="ignore") as f:
                gaps += f.read().count("[указать")
    print(f"\nЗалито {len(wanted)} файлов, {total/1024:.0f} КБ")
    if gaps:
        print(f"ВНИМАНИЕ: на сайте {gaps} заглушек «[указать …]» (ИНН, ОГРН, адрес, e-mail). Сайт выложен, "
              "но по 152-ФЗ и ЗоЗПП эти данные обязательны: отправь владельцу «Уведомление владельцу» "
              "из право.md и перезалей, когда пришлёт.")
    ok = check(slug, title)
    print(f"\n{'Готово' if ok else 'ПРОВЕРКА НЕ ПРОШЛА'}: {PUBLIC_URL}/{slug}/")
    return ok


def is_vasya_dir(sf, path, allow_missing=False):
    try:
        sf.stat(path)
    except IOError:
        return allow_missing
    try:
        sf.stat(posixpath.join(path, MARKER))
        return True
    except IOError:
        return False


def remove(slug):
    if slug in RESERVED:
        sys.exit(f"/{slug}/ трогать нельзя.")
    remote_root = posixpath.join(REMOTE_ROOT, slug)
    t, sf = connect()
    try:
        if not is_vasya_dir(sf, remote_root):
            sys.exit(f"/{slug}/ не сайт Васи (нет метки {MARKER}) или его нет. Удалять не буду.")
        def rmtree(path):
            for attr in sf.listdir_attr(path):
                p = posixpath.join(path, attr.filename)
                if attr.st_mode & 0o40000:
                    rmtree(p)
                else:
                    sf.remove(p)
            sf.rmdir(path)
        rmtree(remote_root)
    finally:
        sf.close(); t.close()
    print(f"Снят с хостинга: {PUBLIC_URL}/{slug}/")


def list_remote():
    t, sf = connect()
    try:
        names = sorted(n for n in sf.listdir(REMOTE_ROOT)
                       if is_vasya_dir(sf, posixpath.join(REMOTE_ROOT, n)))
    finally:
        sf.close(); t.close()
    if not names:
        print("Сайтов Васи на хостинге пока нет")
    for n in names:
        print(f"  {PUBLIC_URL}/{n}/")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    if args[0] == "--list":
        list_remote(); sys.exit(0)
    slug = args[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
        sys.exit("slug — латиница, цифры и дефис: например, yacht-spb")
    if "--check" in args:
        sys.exit(0 if check(slug) else 1)
    if "--remove" in args:
        remove(slug); sys.exit(0)
    sys.exit(0 if deploy(slug) else 1)
