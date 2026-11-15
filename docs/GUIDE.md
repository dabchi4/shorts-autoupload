# Гайд: установка и настройка

Инструкция для Windows. На Linux/macOS всё так же, только пути и активация venv другие.

1. [Установка](#1-установка)
2. [ShardBrowser: профили и вход в аккаунты](#2-shardbrowser-профили-и-вход-в-аккаунты)
3. [Каналы: channels.ini](#3-каналы-channelsini)
4. [Папки с роликами и info.txt](#4-папки-с-роликами-и-infotxt)
5. [Первый запуск YouTube](#5-первый-запуск-youtube)
6. [TikTok через Buffer + Cloudflare R2](#6-tiktok-через-buffer--cloudflare-r2)
7. [Что уже залито: check.py](#7-что-уже-залито-checkpy)
8. [Ежедневная работа](#8-ежедневная-работа)
9. [Требования YouTube и TikTok к аккаунтам](#9-требования-youtube-и-tiktok-к-аккаунтам)
10. [Проблемы и решения](#10-проблемы-и-решения)

---

## 1. Установка

Нужно: Python 3.11+, [ffmpeg](https://ffmpeg.org/) (для `ffprobe` — длительность роликов), git.

```bash
git clone https://github.com/<user>/<repo>.git
cd <repo>
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
copy channels.example.ini channels.ini
```

Браузер ставить не нужно: скрипт подключается к Chromium из ShardBrowser.

## 2. ShardBrowser: профили и вход в аккаунты

[ShardBrowser](https://github.com/ProxyShard/ShardBrowser) — бесплатный браузер с изолированными профилями
и локальным API. Скрипт сам запускает нужный профиль и управляет им по CDP.

1. Установи ShardBrowser, открой **Settings → Automation API** и скопируй токен в `.env` → `SHARD_TOKEN=`.
2. Создай профиль, например `Main`.
3. **Первый запуск профиля — через скрипт**, а не кнопкой в ShardBrowser:
   ```bash
   .venv\Scripts\python tools\probe.py --list              # видны ли профили
   .venv\Scripts\python tools\probe.py --profile Main      # запустит профиль и откроет Studio
   ```
   Окно, открытое кнопкой в интерфейсе ShardBrowser, недоступно для управления (CDP включается только
   при запуске через API). Если профиль уже открыт кнопкой — закрой окно и запусти через скрипт.
4. В открывшемся окне **войди в Google руками**. Скрипт никогда не вводит пароли — так Google не
   ругается на «небезопасный браузер». Вход сохраняется в профиле.
5. Несколько Google-аккаунтов можно держать в одном профиле: `account = 0` у каналов первого вошедшего
   аккаунта, `account = 1` — второго.
6. Удобно переключить интерфейс YouTube Studio на один язык и не менять. Скрипт работает и с русским,
   и с английским (даты формирует через `Intl` браузера), но опирается на id/name элементов, а не на тексты.

## 3. Каналы: channels.ini

Одна секция — одно «назначение»: YouTube-канал или TikTok-аккаунт. Полный список ключей с пояснениями —
в [channels.example.ini](../channels.example.ini). Минимум для YouTube:

```ini
[DEFAULT]
profile = Main
slots = 12:00, 18:00
ai = нет

[мой-канал]
channel_id = UCxxxxxxxxxxxxxxxxxxxxxx   ; из адреса studio.youtube.com/channel/UC…
handle = @my-channel                    ; из youtube.com/channel_switcher
source = D:\Видео\Мой канал
```

- `handle` нужен, чтобы переключаться между каналами одного аккаунта: прямой адрес Studio чужого канала
  сам канал не переключает («У вас нет доступа»), поэтому скрипт ходит через страницу выбора канала.
- `slots` — время публикации по часам компьютера. Ролик без даты получает ближайший свободный слот
  с учётом уже запланированного на канале.
- Пресет канала (`title_suffix`, `description_footer`, `tags`, `game`, `playlist`, `related_video`) — чтобы в
  `info.txt` хватало одного названия. `\n` в `description_footer` — перенос строки.

## 4. Папки с роликами и info.txt

```
<source>\
  2026-11-16 Название ролика\
    Название ролика.mp4
    Название ролика (без субтитров).mp4   ← игнорируется
    обложка.jpg                            ← если картинка одна — станет своей обложкой
    info.txt
    до зазыва\                             ← подпапки игнорируются
```

`info.txt` — все поля необязательны ([пример](../examples/info.txt)):

```
Название: Как пройти игру за 5 минут 😲
Публикация: 2026-11-16 18:00        (или «сейчас»; пусто → слот канала)
Связанное видео: https://youtube.com/shorts/xxxxxxxxxxx
Плейлист: Прохождения
Игра: Minecraft
Теги: игры, спидран
Обложка: обложка.jpg
Описание:
Многострочный текст
со ссылками
```

- Нет `info.txt` или «Названия» — название берётся из имени папки без даты.
- «Описание:» ставь последним: всё после него уходит в описание.
- Неизвестные строки вида «Ключ: значение» и пояснения без двоеточия скрипт пропускает.
- `skip.txt` в папке — ролик никогда не заливается.

## 5. Первый запуск YouTube

```bash
.venv\Scripts\python run.py --profile Main --plan       # что и когда уйдёт (ничего не заливает)
.venv\Scripts\python run.py --profile Main --dry-run    # заполнит всё в Studio, но не нажмёт «Сохранить»
.venv\Scripts\python run.py --profile Main              # залить
.venv\Scripts\python run.py --profile Main --channel мой-канал --limit 1
.venv\Scripts\python run.py --profile Main --only "D:\Видео\Мой канал\2026-11-16 Название" --now
```

- `--dry-run` оставляет на канале приватный черновик — удали его в Studio после проверки.
- После заливки в папке ролика появляется `uploaded.txt` со ссылкой — такие папки больше не трогаются.
- При ошибке — скриншот и HTML в `errors/`, лог — `upload.log`.
- Пока скрипт работает, не трогай окно профиля: он переключает каналы и аккаунты.

## 6. TikTok через Buffer + Cloudflare R2

Buffer — официальный партнёр TikTok: публикация идёт через TikTok Content Posting API, без браузера
и прокси. У Buffer API нет загрузки файлов — он берёт видео по публичной ссылке, поэтому файл сначала
кладётся в Cloudflare R2.

```
видео.mp4 ──► R2 (публичная ссылка) ──► Buffer createPost ──► TikTok в назначенное время
```

### Buffer
1. Зарегистрируйся на buffer.com, подключи TikTok-аккаунты (вход через tiktok.com, пароль в Buffer не вводится).
2. В настройках канала должна быть **автопубликация**, а не напоминания.
3. API-ключ: `publish.buffer.com → Settings → API`.

### Cloudflare R2
1. `dash.cloudflare.com → R2 Object Storage → Create bucket`. Если основной S3-адрес R2 у провайдера не
   открывается (обрыв TLS), выбери **Location → Specify jurisdiction → European Union**.
2. В бакете: **Settings → Public Development URL → Enable**, скопируй `https://pub-….r2.dev`.
3. **Settings → Object lifecycle rules**: удалять объекты старше 30 дней (страховка от мусора).
4. **Manage API tokens → Create API token**, права **Object Read & Write** на этот бакет.

### Конфиг
Скопируй `buffer_config.example.json` в `buffer_config.json` и заполни. `r2_endpoint`:
`https://<account_id>.r2.cloudflarestorage.com` или для EU-бакета `https://<account_id>.eu.r2.cloudflarestorage.com`.

В `channels.ini`:
```ini
[тт-мой-канал]
platform = buffer
profile = Buffer
tiktok_account = my.tiktok      ; имя аккаунта в Buffer, без @
source = D:\Видео\Мой канал     ; можно ту же папку, что у YouTube-канала
```
Несколько секций могут вести в один TikTok-аккаунт (например, две папки) — слоты у них общие.

```bash
.venv\Scripts\python run.py --profile Buffer --plan
.venv\Scripts\python run.py --profile Buffer --dry-run    # R2 + черновик в Buffer, затем всё удаляется
.venv\Scripts\python run.py --profile Buffer
```

- Подпись TikTok = название без `title_suffix`, со смайлом в начале.
- Время уходит в Buffer в UTC; дальше ~10 дней не планируется; на бесплатном Buffer не больше
  10 запланированных постов на канал одновременно (лишние уйдут в следующий запуск).
- Публикует сам Buffer — компьютер в момент публикации может быть выключен.
- Учёт `(post_id, r2_key)` — `buffer_posts.json`; каждый запуск удаляет из R2 файлы уже вышедших постов.
- Метка в папке — `uploaded_tiktok_<аккаунт>.txt`.
- Если в TikTok что-то запланировано руками в самом TikTok, Buffer этого не видит — допиши в метку ролика
  `publish_at` или просто учти при выборе `slots`.

**Лимиты Buffer Free:** 3 канала, 10 запланированных постов на канал, 25 постов в сутки, ~600 запросов
API в сутки. На ролик уходит 3–5 запросов.

### Запасной способ: TikTok через браузер
`platform = tiktok` + отдельный профиль ShardBrowser (вход в TikTok по QR-коду с телефона).
Скрипт работает с TikTok Studio (`tiktok.com/tiktokstudio`). Минусы: через прокси уходит весь
файл видео (на прокси с лимитом трафика это дорого), время в Studio показывается в поясе страны прокси
(скрипт его пересчитывает), для отложенной публикации TikTok один раз спрашивает разрешение хранить видео.

## 7. Что уже залито: check.py

Если часть роликов уже выкладывалась руками, перед первой заливкой разметь их:

```bash
.venv\Scripts\python check.py --profile Main             # ✔ на канале / ? возможно / · не залито
.venv\Scripts\python check.py --profile Main --mark      # метки для точных совпадений
.venv\Scripts\python check.py --profile Main --mark-all  # и для «возможно» (после проверки глазами)
```

Сверка идёт по похожести названия (основы слов) и длительности (±1.6 с), посты старше папки не
учитываются. `run.py` и без меток пропускает похожие ролики, но метки надёжнее.
Для TikTok через Buffer длительность роликов недоступна, сверка идёт только по названию — посмотри
`run.py --profile Buffer --plan` и уже выложенные ролики пометь файлом `uploaded_tiktok_<аккаунт>.txt`
(достаточно пустого JSON `{}`) или `skip.txt`.

## 8. Ежедневная работа

1. Положи папки с роликами в `source` каналов.
2. `run.py --profile Main --plan` → проверь план → `run.py --profile Main`.
3. `run.py --profile Buffer` — те же ролики в отложку TikTok.

Массовая замена «Похожего видео» в роликах за последние N дней:
```bash
.venv\Scripts\python tools\relink.py --profile Main --channel мой-канал --days 14 --list
.venv\Scripts\python tools\relink.py --profile Main --channel мой-канал --days 14 --limit 1
.venv\Scripts\python tools\relink.py --profile Main --channel мой-канал --days 14
```

## 9. Требования YouTube и TikTok к аккаунтам

- **Своя обложка Shorts, «Похожее видео», кликабельные ссылки в описании** — нужен подтверждённый телефон
  (расширенные функции) на каждом канале. Без этого скрипт остановится с понятной ошибкой.
- **Монетизированные каналы** — обязательная анкета «Требования рекламодателей». Скрипт отмечает только
  «Ничего из указанного выше» и только при `ad_rating = ничего`. Оценку после отправки не изменить.
- **Ролики > 1 мин с заявкой Content ID** YouTube блокирует как Shorts.
- **Один и тот же ролик на нескольких TikTok-аккаунтах** TikTok может посчитать дубликатом и урезать охват.

## 10. Проблемы и решения

| Симптом | Причина / решение |
|---|---|
| «Профиль уже открыт из окна ShardBrowser» | закрой окно профиля, запусти через скрипт |
| «У вас нет доступа к этой странице» в Studio | активен другой канал/аккаунт — проверь `handle` и `account` |
| Видео > 50 МБ не грузится | нужен patchright/playwright ≥ 1.58 (`connect_over_cdp(is_local=True)`) |
| Капча Google в новом профиле | новый профиль без истории или общий IP (VPN) — для TikTok/YouTube Google-поиск не нужен |
| TikTok Studio: «Unexpected Application Error» | не блокировать картинки/шрифты — только видео (`save_traffic("light")`) |
| `403` на `pub-….r2.dev` | Cloudflare режет User-Agent `Python-urllib` — скрипт шлёт свой |
| `SSLV3_ALERT_HANDSHAKE_FAILURE` к R2 | бакет с юрисдикцией EU и адрес `….eu.r2.cloudflarestorage.com` |
| Ролик остался черновиком | закрыли вкладку до конца загрузки; скрипт ждёт 100% — не трогай окно |
