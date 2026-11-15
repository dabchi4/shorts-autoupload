# Исследование: заливка Shorts через YouTube Studio в браузере

Дата: 2026-11-14. Источники — открытые репозитории и справка Google (ссылки ниже).

## Итоговый стек

- **Python + patchright** (стелс-форк Playwright), подключение к профилю ShardBrowser:
  `POST http://127.0.0.1:40325/profiles/{id}/start` → `cdp.web_socket_debugger_url` →
  `connect_over_cdp(url, is_local=True)` (нужен Playwright/patchright ≥ 1.58, иначе лимит 50 МБ на файл).
- Работать в `browser.contexts[0]` (там cookies профиля), одна вкладка Studio, `bring_to_front()`.
- База для кода: `dreammis/social-auto-upload` (MIT, 16.5k★, активен) — `uploader/youtube_uploader/main.py` + PR #309.
- Интерфейс Studio на обоих аккаунтах — **английский** (форматы даты/времени и текстовые фолбэки предсказуемы).

## Чего избегать

| Ошибка | Что происходит | Что делаем вместо |
|---|---|---|
| Скрипт вводит логин/пароль | «This browser may not be secure», капча, «Verify it's you» | Логин руками один раз, живой профиль ShardBrowser |
| Headless | Блок «unsupported browser» | Только видимое окно |
| Абсолютные XPath, селекторы по тексту/aria | Ломаются при каждом обновлении Studio и на неанглийском UI | `#id`, `name=`, `test-id`, `input[type=file]` |
| Фиксированные `sleep` | Гонки, клик в оверлей | Ожидание конкретного состояния элемента |
| Закрыть вкладку до 100% загрузки | Видео остаётся черновиком | Ждать `ytcp-video-upload-progress` без `[uploading]` + проверка статуса в «Контенте» |
| Посимвольный ввод `#тегов` | Всплывающий автокомплит перекрывает кнопки | `fill()` + blur/Escape |
| Внутренний youtubei API снаружи браузера как основной путь | Токен сессии живёт от 15 мин до недели, меняется формат | Только UI; внутренний API — запасной вариант для одного поля |
| `new_context()` | Нет cookies, разлогин | `contexts[0]` |
| Глотать все исключения | «Успех», а видео не опубликовано | Скриншот + HTML в `errors/`, сверка полей после ввода |

## Поля и селекторы (стартовые, проверить на живом DOM)

| Поле | Селектор / приём | Подводные камни |
|---|---|---|
| Файл | `input[type=file]` → `set_input_files(path)` | Путь абсолютный, на той же машине |
| Название | `#title-textarea #textbox` → click, Ctrl+A, Delete, `fill()` | ≤100 символов, нельзя `<` `>` |
| Описание | `#description-textarea #textbox` | ≤5000 символов |
| Обложка | `ytcp-thumbnail-uploader input[type=file]` / `#file-loader` | Нет, если канал не верифицирован; признак `[feature-disabled]` |
| Плейлист | `ytcp-video-metadata-playlists ytcp-dropdown-trigger` → `#search-input` → чекбокс по имени → Done | Диалог блокирует дальнейшие шаги, если не закрыть |
| Не для детей | `tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']` | Обязательное; имя менялось — держать фолбэк |
| Изменённый контент | `[name='VIDEO_HAS_ALTERED_CONTENT_NO']` | Новое поле |
| Показать ещё | `#toggle-button` | |
| Теги | `#tags-container #text-input`, через запятую с запятой в конце | ≤500 символов суммарно |
| Категория → Игра | `#category ytcp-text-dropdown-trigger` → Gaming → `.ytcp-form-gaming input` → выбрать подсказку | Свободный текст не сохраняется, надо кликнуть вариант; у вариантов JSON в `test-id` (`title`, `year`) |
| Далее / Готово | `#next-button:not([disabled])` ×3, `#done-button` | Диалог «pre-checks» / «Publish anyway» / «Got it» |
| Отложенная публикация | `#schedule-radio-button` → `#datepicker-trigger` → ввод даты (en: `Nov 15, 2026`) Enter → `#time-of-day-container input` (`6:00 PM`) Enter | Формат зависит от языка UI; часовой пояс; после ввода прочитать значение обратно |
| Ссылка на видео | `a.ytcp-video-info` / `[href*="/shorts/"]`, ждать заполнения href | Нужна для этапа 2 |
| Связанное видео | По справке Google — на странице редактирования `studio.youtube.com/video/<id>/edit` → «Показать ещё» → Related video | Селектор неизвестен — снять с живого DOM. Запасной путь: `metadata_update` с `videoLinkageShortsAttribution.targetExternalVideoId` (DominikPeters/scotus-videos) |

## Требования YouTube к Shorts

- Shorts = вертикальное/квадратное видео ≤ 3 мин (автоматически). Shorts > 1 мин с активной Content ID-заявкой блокируются.
- Своя обложка для Shorts — только в Studio на компьютере, нужен подтверждённый телефоном аккаунт; раскатка с июля 2026, сначала для партнёров (не подтверждено официально). 9:16, JPG/PNG.
- «Связанное видео» — нужен расширенный доступ к функциям; ролик своего канала, публичный или по ссылке.
- Есть дневной лимит загрузок — ловить сообщение «Daily upload limit reached».

## Ссылки

- https://github.com/dreammis/social-auto-upload (основа)
- https://github.com/dreammis/social-auto-upload/pull/309 (диалоги, проверка расписания, бренд-каналы)
- https://github.com/dreammis/social-auto-upload/pull/247 (подключение по CDP)
- https://github.com/fawazahmed0/youtube-uploader (выбор игры, ожидание ссылки)
- https://github.com/tqk2811/YoutubeUploadSelenium (защиты от ошибок, обложка, прогресс)
- https://github.com/oil-oil/video-publisher-skill (сверка полей, режим «остановиться перед публикацией»)
- https://github.com/danilokhury/Synabun (определение локали для даты)
- https://github.com/DominikPeters/scotus-videos (связанное видео через metadata_update)
- https://support.google.com/youtube/answer/14075157 (связанное видео)
- https://support.google.com/youtube/answer/72431 (обложки)
- https://support.google.com/youtube/answer/12779649 (что считается Shorts)
- https://support.google.com/youtube/answer/1270709 (отложенная публикация)
