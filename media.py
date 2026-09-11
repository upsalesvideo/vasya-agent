#!/usr/bin/env python
r"""Медиа для сайтов Васи через Genosai Public API (https://genosai.io/docs/public-api).

Ключ: E:\Claude\genosai-cli\api_key.txt (sdk_live_…) или переменная GENOSAI_API_KEY.
Хост: https://api.genosai.io (ключи sdk_live_ работают только с ним).

Команды (все пишут результат в папку проекта и в манифест медиа/manifest.json):

  python media.py photo <slug> "<промпт>" --name hero [--ar 16:9] [--model chatgpt-image-2] [--ref файл ...]
  python media.py edit <slug> <name|файл> "<что изменить>" [--model nano-banana-2] [--ref файл ...]
  python media.py video <slug> "<промпт>" --name promo [--model veo-3.1-lite] [--duration 6] [--ar 16:9] [--res 720p] [--image кадр.jpg] [--no-audio]
  python media.py music <slug> "<описание трека>" --name bg [--instrumental] [--duration 90] [--style "..." --title "..." --lyrics файл.txt]
  python media.py voice <slug> "<текст>" --name intro [--voice Charon] [--style Newscaster]
  python media.py own <slug> <name> <файл>          свой файл владельца → в assets под тем же именем
  python media.py versions <slug> <name>            история версий медиа
  python media.py restore <slug> <name> <n>         вернуть версию n
  python media.py batch <slug> план.json            несколько медиа сразу: отправка с интервалом 1 с, ожидание параллельно
  python media.py list <slug>                       что есть в манифесте
  python media.py estimate photo|video|music|voice [--model ...]   цена в кредитах, без генерации
  python media.py models | balance

Каждый вызов:
  1. Загружает референсы (POST /v1/uploads — одноразовые URL, на каждую генерацию заново).
  2. Создаёт задачу (POST /v1/createTask) и ждёт (GET /v1/taskInfo), тайм-аут 5 мин фото / 20 мин видео и музыка.
     Фото, зависшее дольше 5 минут, перезапускается один раз (правило из памяти).
  3. Скачивает оригинал в сайты/<slug>/медиа/<name>.v<N>.<ext> и делает веб-версию в site/assets/:
       фото  → <name>.webp (до 1600 px по длинной стороне) + <name>-800.webp для мобильных
       видео → <name>.mp4 (h264_nvenc, ≤1280 px, без звука если --no-audio) + <name>-poster.webp
       музыка → <name>.mp3 (первый трек; второй вариант лежит рядом как <name>-alt.mp3 в медиа/)
       голос  → <name>.mp3
  4. Пишет в медиа/manifest.json: имя, тип, модель, промпт, taskId, цена, версия, s3-ссылка.
"""
import argparse, json, mimetypes, os, posixpath, shutil, subprocess, sys, time, uuid
from datetime import datetime

import requests

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

API = "https://api.genosai.io"
HERE = os.path.dirname(os.path.abspath(__file__))
SITES = os.path.join(HERE, "сайты")
# Где искать ключ sdk_live_… (первый найденный): переменная окружения, файл рядом со скриптом
# (genosai_key.txt — в .gitignore), файл в домашней папке, старый путь Антона.
KEY_FILES = [
    os.path.join(HERE, "genosai_key.txt"),
    os.path.expanduser("~/.secrets/genosai_api_key.txt"),
    r"E:\Claude\genosai-cli\api_key.txt",
]

DEFAULTS = {
    "photo": "chatgpt-image-2",
    "edit": "nano-banana-2",
    "video": "veo-3.1-lite",
    "music": "suno-v5.5",
    "voice": "gemini-3.1-flash-tts",
}
PHOTO_TIMEOUT, LONG_TIMEOUT = 5 * 60, 20 * 60


# ---------- API ----------

def key():
    k = os.environ.get("GENOSAI_API_KEY", "").strip()
    for p in KEY_FILES:
        if k:
            break
        if os.path.exists(p):
            k = open(p, encoding="utf-8").read().strip()
    if not k.startswith("sdk_"):
        sys.exit("Нет ключа Genosai. Создай ключ в профиле genosai.io → «API-ключи» и положи его "
                 f"одной строкой в {KEY_FILES[0]} (или в переменную GENOSAI_API_KEY).")
    return k


def api(method, path, **kw):
    h = {"Authorization": "Bearer " + key()}
    h.update(kw.pop("headers", {}))
    for attempt in range(4):
        try:
            r = requests.request(method, API + path, headers=h, timeout=120, **kw)
            break
        except requests.exceptions.RequestException as e:
            if attempt == 3:
                sys.exit(f"Сеть: {API}{path} недоступен после 4 попыток: {e}")
            time.sleep(2 * (attempt + 1))
            for f in (kw.get("files") or {}).values():
                if hasattr(f[1], "seek"):
                    f[1].seek(0)
    if r.status_code >= 400:
        try:
            d = r.json()
        except ValueError:
            d = {"error": r.text[:300]}
        sys.exit(f"Ошибка API {r.status_code}: {d.get('message') or d.get('error') or d}")
    return r.json()


def upload(path):
    if path.startswith("http"):
        return path
    if not os.path.exists(path):
        sys.exit(f"Нет файла: {path}")
    with open(path, "rb") as f:
        d = api("POST", "/v1/uploads", files={"file": (os.path.basename(path), f,
                 mimetypes.guess_type(path)[0] or "application/octet-stream")})
    url = d.get("url") or (d.get("data") or {}).get("url")
    if not url:
        sys.exit("uploads не вернул url: " + json.dumps(d, ensure_ascii=False))
    print(f"  референс загружен: {os.path.basename(path)}")
    return url


def create(model, inp):
    d = api("POST", "/v1/createTask", json={"model": model, "input": inp},
            headers={"Content-Type": "application/json; charset=utf-8"})
    tid = (d.get("data") or {}).get("taskId")
    if not tid:
        sys.exit("Нет taskId: " + json.dumps(d, ensure_ascii=False))
    print(f"  задача {tid} ({model})")
    return tid


def download(url, dest):
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    return dest


# ---------- проект и манифест ----------

def paths(slug):
    root = os.path.join(SITES, slug)
    if not os.path.isdir(root):
        sys.exit(f"Нет проекта {root}")
    media = os.path.join(root, "медиа")
    assets = os.path.join(root, "site", "assets")
    os.makedirs(media, exist_ok=True)
    os.makedirs(assets, exist_ok=True)
    return root, media, assets


def load_manifest(media):
    p = os.path.join(media, "manifest.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save_manifest(media, m):
    with open(os.path.join(media, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


def next_version(m, name):
    return len(m.get(name, {}).get("versions", [])) + 1


def record(media, name, kind, entry):
    m = load_manifest(media)
    item = m.setdefault(name, {"type": kind, "versions": []})
    item["type"] = kind
    entry["n"] = len(item["versions"]) + 1
    entry["at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    item["versions"].append(entry)
    item["current"] = entry["n"]
    save_manifest(media, m)
    return entry["n"]


# ---------- веб-версии ----------

def web_image(src, assets, name):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    out = []
    for suffix, width in (("", 1600), ("-800", 800)):
        w, h = im.size
        img = im if w <= width else im.resize((width, round(h * width / w)), Image.LANCZOS)
        dest = os.path.join(assets, f"{name}{suffix}.webp")
        img.save(dest, "WEBP", quality=82, method=6)
        out.append(dest)
    return out


def web_video(src, assets, name, no_audio):
    dest = os.path.join(assets, f"{name}.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-vf", "scale='min(1280,iw)':-2", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "26",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd += ["-an"] if no_audio else ["-c:a", "aac", "-b:a", "128k"]
    subprocess.run(cmd + [dest], check=True)
    poster = os.path.join(assets, f"{name}-poster.webp")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", dest,
                    "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", poster], check=True)
    return [dest, poster]


def web_audio(src, assets, name):
    dest = os.path.join(assets, f"{name}.mp3")
    if src.lower().endswith(".mp3"):
        shutil.copy(src, dest)
    else:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                        "-c:a", "libmp3lame", "-b:a", "160k", dest], check=True)
    return [dest]


def ext_of(url, default):
    e = os.path.splitext(posixpath.basename(url.split("?")[0]))[1].lower()
    return e or default


# ---------- задания (общий код для одиночных команд и пакета) ----------

SUBMIT_INTERVAL = 1.0   # секунда между отправками задач в Genosai (параллельная генерация)
POLL_INTERVAL = 3.0


def build_job(kind, spec, media):
    """spec: dict с полями команды. Возвращает job: {kind, name, model, input, timeout, retry, meta}."""
    name = spec.get("name") or sys.exit(f"У задания нет name: {spec}")
    if kind == "photo":
        model = spec.get("model") or DEFAULTS["photo"]
        inp = {"prompt": spec["prompt"]}
        for k, f in (("ar", "aspect_ratio"), ("res", "resolution")):
            if spec.get(k):
                inp[f] = spec[k]
        if spec.get("ref"):
            inp["image_urls"] = [upload(r) for r in spec["ref"]]
        return dict(kind="photo", name=name, model=model, input=inp, timeout=PHOTO_TIMEOUT, retry=True,
                    meta={"prompt": spec["prompt"], "refs": spec.get("ref") or []})
    if kind == "edit":
        m = load_manifest(media)
        src = spec["source"]
        if not os.path.exists(src):
            item = m.get(src) or sys.exit(f"Нет ни файла, ни медиа с именем {src}")
            src = item["versions"][item["current"] - 1]["orig"]
        model = spec.get("model") or DEFAULTS["edit"]
        inp = {"prompt": spec["prompt"], "image_urls": [upload(src)] + [upload(r) for r in (spec.get("ref") or [])]}
        ar = spec.get("ar") or ("auto" if "auto" in model_options(model, "aspect_ratio") else None)
        if ar:
            inp["aspect_ratio"] = ar  # auto = сохранить пропорции исходника
        return dict(kind="photo", name=name, model=model, input=inp, timeout=PHOTO_TIMEOUT, retry=True,
                    meta={"prompt": spec["prompt"], "edit_of": src, "refs": spec.get("ref") or []})
    if kind == "video":
        model = spec.get("model") or DEFAULTS["video"]
        inp = {"prompt": spec.get("prompt", "")}
        for k, f in (("ar", "aspect_ratio"), ("res", "resolution")):
            if spec.get(k):
                inp[f] = spec[k]
        if spec.get("duration"):
            inp["duration"] = int(spec["duration"])
        if spec.get("no_audio") and model_has(model, "generate_audio"):
            inp["generate_audio"] = False  # модели без поля (grok, gemini-omni, minimax) отдают 400
        if spec.get("image"):
            inp["image_urls"] = [upload(i) for i in spec["image"]]
        return dict(kind="video", name=name, model=model, input=inp, timeout=LONG_TIMEOUT, retry=False,
                    meta={"prompt": inp["prompt"], "images": spec.get("image") or [], "duration": spec.get("duration"),
                          "no_audio": bool(spec.get("no_audio"))})
    if kind == "music":
        model = spec.get("model") or DEFAULTS["music"]
        inp = {"prompt": spec.get("prompt", "")}
        if spec.get("instrumental"):
            inp["instrumental"] = True
        if spec.get("style") or spec.get("title") or spec.get("lyrics"):
            if not (spec.get("style") and spec.get("title")):
                sys.exit("Кастомный режим музыки: нужны и style, и title")
            inp.update({"custom_mode": True, "style": spec["style"], "title": spec["title"]})
            if spec.get("lyrics"):
                inp["prompt"] = open(spec["lyrics"], encoding="utf-8").read()
            if spec.get("duration"):
                inp["duration"] = int(spec["duration"])
            if spec.get("vocal"):
                inp["vocal_gender"] = spec["vocal"]
        return dict(kind="music", name=name, model=model, input=inp, timeout=LONG_TIMEOUT, retry=False,
                    meta={"prompt": inp["prompt"], "input": inp})
    if kind == "voice":
        model = spec.get("model") or DEFAULTS["voice"]
        inp = {"text": spec["text"]}
        for k in ("voice", "style", "accent", "pace"):
            if spec.get(k):
                inp[k] = spec[k]
        return dict(kind="voice", name=name, model=model, input=inp, timeout=PHOTO_TIMEOUT, retry=False,
                    meta={"text": spec["text"], "input": inp})
    sys.exit(f"Неизвестный тип задания: {kind}")


def finish_job(job, urls, cost, media, assets):
    """Скачивает результат, делает веб-версию, пишет манифест. Возвращает (n, orig, files)."""
    kind, name = job["kind"], job["name"]
    n = next_version(load_manifest(media), name)
    entry = dict(job["meta"], model=job["model"], taskId=job["taskId"], cost=cost, url=urls[0])
    if kind == "photo":
        orig = download(urls[0], os.path.join(media, f"{name}.v{n}{ext_of(urls[0], '.png')}"))
        files = web_image(orig, assets, name)
    elif kind == "video":
        orig = download(urls[0], os.path.join(media, f"{name}.v{n}{ext_of(urls[0], '.mp4')}"))
        files = web_video(orig, assets, name, job["meta"].get("no_audio", False))
    elif kind == "music":
        orig = download(urls[0], os.path.join(media, f"{name}.v{n}{ext_of(urls[0], '.mp3')}"))
        if len(urls) > 1:
            entry["alt"] = download(urls[1], os.path.join(media, f"{name}.v{n}-alt{ext_of(urls[1], '.mp3')}"))
            entry["url_alt"] = urls[1]
        files = web_audio(orig, assets, name)
    else:
        orig = download(urls[0], os.path.join(media, f"{name}.v{n}{ext_of(urls[0], '.mp3')}"))
        files = web_audio(orig, assets, name)
    entry["orig"] = orig
    record(media, name, kind, entry)
    return n, orig, files


def run_jobs(jobs, media, assets):
    """Отправляет все задачи с интервалом SUBMIT_INTERVAL, затем ждёт их параллельно."""
    for i, job in enumerate(jobs):
        if i:
            time.sleep(SUBMIT_INTERVAL)
        job["taskId"] = create(job["model"], job["input"])
        job["t0"] = time.time()
        job["retried"] = False
    pending = list(jobs)
    done, failed = [], []
    while pending:
        time.sleep(POLL_INTERVAL)
        for job in list(pending):
            d = api("GET", "/v1/taskInfo", params={"taskId": job["taskId"]}).get("data", {})
            st = d.get("status")
            if st == "succeeded":
                urls = (d.get("result") or {}).get("media_urls") or []
                if not urls:
                    job["error"] = "succeeded, но media_urls пуст"; failed.append(job); pending.remove(job); continue
                n, orig, files = finish_job(job, urls, d.get("cost"), media, assets)
                job.update(n=n, orig=orig, files=files, cost=d.get("cost"), s3=urls[0])
                print(f"  + {job['name']} v{n} за {int(time.time()-job['t0'])} с, {d.get('cost')} кр -> {orig}")
                done.append(job); pending.remove(job)
            elif st in ("failed", "error", "canceled"):
                job["error"] = f"{st} {d.get('message') or ''}".strip(); failed.append(job); pending.remove(job)
                print(f"  x {job['name']}: {job['error']}")
            elif time.time() - job["t0"] > job["timeout"]:
                if job["retry"] and not job["retried"]:
                    print(f"  ~ {job['name']} зависло дольше {job['timeout']//60} мин, перезапускаю")
                    job["taskId"] = create(job["model"], job["input"]); job["t0"] = time.time(); job["retried"] = True
                else:
                    job["error"] = f"тайм-аут, taskId {job['taskId']}"; failed.append(job); pending.remove(job)
                    print(f"  x {job['name']}: {job['error']}")
        if pending and len(jobs) > 1:
            print(f"  ... ждём {len(pending)}: " + ", ".join(j["name"] for j in pending), flush=True)
        elif pending:
            print(".", end="", flush=True)
    return done, failed


def run_single(a, kind, spec):
    root, media, assets = paths(a.slug)
    job = build_job(kind, spec, media)
    done, failed = run_jobs([job], media, assets)
    if failed:
        sys.exit(f"{job['name']}: {failed[0]['error']}")
    j = done[0]
    print(f"\n{j['name']} v{j['n']}: {j['orig']}\n  веб: " + ", ".join(j["files"]) + f"\n  s3: {j['s3']}")


# ---------- команды ----------

def cmd_photo(a):
    run_single(a, "photo", dict(name=a.name, prompt=a.prompt, model=a.model, ar=a.ar, res=a.res, ref=a.ref))


def cmd_edit(a):
    name = a.name or (a.source if not os.path.exists(a.source) else os.path.splitext(os.path.basename(a.source))[0])
    run_single(a, "edit", dict(name=name, source=a.source, prompt=a.prompt, model=a.model, ar=a.ar, ref=a.ref))


def cmd_video(a):
    run_single(a, "video", dict(name=a.name, prompt=a.prompt, model=a.model, ar=a.ar, res=a.res,
                                duration=a.duration, image=a.image, no_audio=a.no_audio))


def cmd_music(a):
    run_single(a, "music", dict(name=a.name, prompt=a.prompt, model=a.model, instrumental=a.instrumental,
                                duration=a.duration, style=a.style, title=a.title, lyrics=a.lyrics, vocal=a.vocal))


def cmd_voice(a):
    run_single(a, "voice", dict(name=a.name, text=a.text, model=a.model, voice=a.voice, style=a.style,
                                accent=a.accent, pace=a.pace))


def cmd_batch(a):
    """План: JSON-список заданий. Каждое: {"kind": "photo|edit|video|music|voice", "name": "...", ...поля как у команд}.
    Пример: [{"kind":"photo","name":"hero","prompt":"…","ar":"16:9"},
             {"kind":"video","name":"promo","prompt":"…","duration":6,"no_audio":true}]"""
    root, media, assets = paths(a.slug)
    plan = json.load(open(a.plan, encoding="utf-8"))
    if isinstance(plan, dict):
        plan = plan.get("items") or plan.get("jobs") or sys.exit("В плане нет списка items")
    # одно имя несколько раз = варианты одного места, лягут версиями v1, v2, v3 в порядке готовности
    print(f"Пакет: {len(plan)} заданий, отправка с интервалом {SUBMIT_INTERVAL:g} с")
    jobs = [build_job(p["kind"], p, media) for p in plan]
    done, failed = run_jobs(jobs, media, assets)
    total = sum((j.get("cost") or 0) for j in done)
    print(f"\nГотово {len(done)} из {len(jobs)}, потрачено {total:g} кредитов")
    for j in done:
        print(f"  {j['name']:16} {j['kind']:6} v{j['n']}  {j['cost']} кр  {j['files'][0]}\n{'':20}s3: {j['s3']}")
    if failed:
        print("\nНе получилось:")
        for j in failed:
            print(f"  {j['name']:16} {j['error']}")
        sys.exit(1)


def cmd_own(a):
    root, media, assets = paths(a.slug)
    if not os.path.exists(a.file):
        sys.exit(f"Нет файла {a.file}")
    ext = os.path.splitext(a.file)[1].lower()
    n = next_version(load_manifest(media), a.name)
    orig = os.path.join(media, f"{a.name}.v{n}{ext}")
    shutil.copy(a.file, orig)
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"):
        kind, files = "photo", web_image(orig, assets, a.name)
    elif ext in (".mp4", ".mov", ".webm", ".mkv"):
        kind, files = "video", web_video(orig, assets, a.name, False)
    elif ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
        kind, files = "music", web_audio(orig, assets, a.name)
    else:
        sys.exit(f"Не знаю, что делать с {ext}")
    record(media, a.name, kind, {"model": "own", "prompt": "файл владельца", "source": a.file, "orig": orig, "cost": 0})
    print(f"{a.name} v{n} (свой файл): веб: " + ", ".join(files))


def _restore_files(assets, name, item, n):
    v = item["versions"][n - 1]
    orig = v["orig"]
    if not os.path.exists(orig):
        sys.exit(f"Файл версии {n} не найден: {orig}")
    kind = item["type"]
    if kind == "photo":
        return web_image(orig, assets, name)
    if kind == "video":
        return web_video(orig, assets, name, v.get("no_audio", False))
    return web_audio(orig, assets, name)


def cmd_restore(a):
    root, media, assets = paths(a.slug)
    m = load_manifest(media)
    item = m.get(a.name) or sys.exit(f"Нет медиа {a.name}")
    n = int(a.n)
    if not 1 <= n <= len(item["versions"]):
        sys.exit(f"У {a.name} версии 1..{len(item['versions'])}")
    files = _restore_files(assets, a.name, item, n)
    item["current"] = n
    save_manifest(media, m)
    print(f"{a.name}: вернул версию {n} -> " + ", ".join(files))


def cmd_versions(a):
    root, media, assets = paths(a.slug)
    item = load_manifest(media).get(a.name) or sys.exit(f"Нет медиа {a.name}")
    for v in item["versions"]:
        cur = "*" if v["n"] == item["current"] else " "
        print(f"{cur} v{v['n']}  {v['at']}  {v.get('model')}  {v.get('cost') or 0} кр  {(v.get('prompt') or v.get('text') or '')[:70]}")


def cmd_list(a):
    root, media, assets = paths(a.slug)
    m = load_manifest(media)
    if not m:
        print("Медиа пока нет")
    for name, item in m.items():
        v = item["versions"][item["current"] - 1]
        print(f"{name:16} {item['type']:6} v{item['current']}/{len(item['versions'])}  {v.get('model')}  {(v.get('prompt') or v.get('text') or '')[:60]}")


_catalog = None


def models_catalog():
    global _catalog
    if _catalog is None:
        _catalog = api("GET", "/v1/models")
    return _catalog


def _model(model):
    for items in models_catalog().values():
        if isinstance(items, list):
            for m in items:
                if m.get("id") == model:
                    return m
    return None


def model_has(model, field):
    m = _model(model)
    return bool(m) and field in m.get("input_options", {})


def model_options(model, field):
    m = _model(model)
    return ((m or {}).get("input_options", {}).get(field) or {}).get("options") or []


def cmd_models(a):
    cat = models_catalog()
    for kind in ("photo", "video", "music", "tts"):
        items = cat.get(kind) or []
        if not items:
            continue
        print(kind.upper())
        for m in items:
            o = m.get("input_options", {})
            extra = []
            for k in ("duration", "resolution", "aspect_ratio"):
                if k in o and o[k].get("options"):
                    extra.append(f"{k}={'/'.join(map(str, o[k]['options']))}")
            cost = m.get("cost_credits_default")
            if m.get("pricing"):
                p = m["pricing"]
                cost = f"{p.get('credits_per_unit')} кр/{p.get('chars_per_unit')} симв, мин {p.get('min_credits')}"
            print(f"  {m['id']:22} {str(cost):>8}  {' '.join(extra)}")


def cmd_estimate(a):
    kind = {"photo": "photo", "edit": "photo", "video": "video", "music": "music", "voice": "tts"}[a.kind]
    model = a.model or DEFAULTS[a.kind]
    m = _model(model)
    if not m:
        sys.exit(f"Модель {model} не найдена")
    if kind == "tts":
        p = m["pricing"]
        print(f"{model}: {p['credits_per_unit']} кр за {p['chars_per_unit']} символов, минимум {p['min_credits']}")
    else:
        print(f"{model}: {m.get('cost_credits_default')} кредитов за генерацию (базовая цена; видео зависит от длительности/разрешения)")


def cmd_balance(a):
    d = api("GET", "/v1/balance")
    print(f"Баланс Genosai: {d.get('total')} кредитов")


def main():
    p = argparse.ArgumentParser(description="Медиа Васи через Genosai API")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models").set_defaults(fn=cmd_models)
    sub.add_parser("balance").set_defaults(fn=cmd_balance)
    e = sub.add_parser("estimate"); e.add_argument("kind", choices=["photo", "edit", "video", "music", "voice"]); e.add_argument("--model"); e.set_defaults(fn=cmd_estimate)

    proj = argparse.ArgumentParser(add_help=False); proj.add_argument("slug")

    s = sub.add_parser("photo", parents=[proj]); s.add_argument("prompt"); s.add_argument("--name", required=True)
    s.add_argument("--model"); s.add_argument("--ar"); s.add_argument("--res"); s.add_argument("--ref", action="append"); s.set_defaults(fn=cmd_photo)

    s = sub.add_parser("edit", parents=[proj]); s.add_argument("source", help="имя медиа из манифеста или путь к файлу"); s.add_argument("prompt")
    s.add_argument("--name"); s.add_argument("--model"); s.add_argument("--ar"); s.add_argument("--ref", action="append"); s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("video", parents=[proj]); s.add_argument("prompt"); s.add_argument("--name", required=True)
    s.add_argument("--model"); s.add_argument("--ar"); s.add_argument("--res"); s.add_argument("--duration")
    s.add_argument("--image", action="append", help="кадр(ы): 1 - первый, 2 - первый и последний"); s.add_argument("--no-audio", dest="no_audio", action="store_true"); s.set_defaults(fn=cmd_video)

    s = sub.add_parser("music", parents=[proj]); s.add_argument("prompt"); s.add_argument("--name", required=True)
    s.add_argument("--model"); s.add_argument("--instrumental", action="store_true"); s.add_argument("--duration")
    s.add_argument("--style"); s.add_argument("--title"); s.add_argument("--lyrics"); s.add_argument("--vocal", choices=["m", "f"]); s.set_defaults(fn=cmd_music)

    s = sub.add_parser("voice", parents=[proj]); s.add_argument("text"); s.add_argument("--name", required=True)
    s.add_argument("--model"); s.add_argument("--voice"); s.add_argument("--style"); s.add_argument("--accent"); s.add_argument("--pace"); s.set_defaults(fn=cmd_voice)

    s = sub.add_parser("batch", parents=[proj], help="пакет заданий из JSON-плана, параллельно"); s.add_argument("plan"); s.set_defaults(fn=cmd_batch)

    s = sub.add_parser("own", parents=[proj]); s.add_argument("name"); s.add_argument("file"); s.set_defaults(fn=cmd_own)
    s = sub.add_parser("versions", parents=[proj]); s.add_argument("name"); s.set_defaults(fn=cmd_versions)
    s = sub.add_parser("restore", parents=[proj]); s.add_argument("name"); s.add_argument("n"); s.set_defaults(fn=cmd_restore)
    sub.add_parser("list", parents=[proj]).set_defaults(fn=cmd_list)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
