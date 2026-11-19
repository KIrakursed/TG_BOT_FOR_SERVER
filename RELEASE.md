# Релиз: как проверять и как откатывать

Документ для того, кто (или что) выкатывает бота. Всё, что здесь написано, прогнано руками
на проде `130.61.47.113` в этой итерации.

## 1. Ворота качества (локально)

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W ignore::DeprecationWarning
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m alembic check      # дрейф моделей и миграций
```

Текущее состояние: **516 passed, 1 skipped**, ruff чистый, `alembic check` — «No new upgrade
operations detected». Любая правка, роняющая эти три пункта, не едет на сервер: `deploy.sh`
выполняет те же команды до `systemctl restart` и падает раньше рестарта.

## 2. Выкатка

Автоматически (штатный путь, как только появится живой приватный репозиторий):

```
git push origin main      # сервер сам: kvartira-autopull.timer, раз в 2 минуты
journalctl -u kvartira-autopull -n 30
```

Вручную (когда GitHub недоступен, как сейчас):

```bash
# на локальной машине
git bundle create release.bundle main
scp release.bundle kvartira-server:/home/ubuntu/release.bundle

# на сервере
cd /home/ubuntu/telegram_bot_krisha.kz
git fetch /home/ubuntu/release.bundle main
git merge --ff-only FETCH_HEAD
.venv/bin/pip install -q -r requirements-dev.txt
.venv/bin/python -m pytest -q -W ignore::DeprecationWarning
.venv/bin/python -m ruff check app tests
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic check
sudo systemctl restart kvartira-bot
systemctl is-active kvartira-bot
```

## 3. Откат

База и код откатываются раздельно, порядок важен:

```bash
# 1) код на предыдущий коммит
git log --oneline -5
git reset --hard <commit>
sudo systemctl restart kvartira-bot

# 2) если виновата миграция
.venv/bin/python -m alembic downgrade -1
```

Миграция `0010_indexes_rooms_min` обратима: снимает индексы, возвращает старые и удаляет
колонку `searches.rooms_min`. Данные не теряются — колонка необязательная.

Перед любой миграцией на проде:

```bash
pg_dump -Fc kv_bot > ~/backups/kv_bot_$(date +%Y%m%d-%H%M).dump
```

## 4. Что проверено на этой итерации

| Проверка | Результат |
|---|---|
| Тесты на сервере | 516 passed, 1 skipped |
| ruff на сервере | All checks passed |
| Миграция на проде | `0009 → 0010`, применена |
| `alembic check` | без дрейфа |
| Живой поиск: без животных / без залога / без комиссии / без риэлтора | 10 совпадений в каждом (до этого «без залога» и «без комиссии» давали 0) |
| Монитор после фикса JSON | `search_id=7 notified=0 23.0s`, `search_id=4 25.4s`, ошибок нет |
| Сервис после рестарта | active, hardening применён (`ProtectSystem=strict`, `NoNewPrivileges=yes`) |
| Ошибки в журнале за 3 минуты после рестарта | нет |

## 5. Известные ограничения (осознанно НЕ сделано в этом релизе)

Это не забытое, а отложенное — с причиной:

1. **Монитор отправляет уведомления, держа транзакцию.** Сеть уже вынесена из транзакции
   (было критично), но сама отправка сообщений идёт внутри короткой сессии. Это секунды, а не
   минуты; полноценный claim-then-send требует отдельного поля статуса.
2. **`is_active=False` нигде не ставится.** Заброшенные объявления не помечаются — для этого
   нужен проход по выдаче с проверкой живости страницы (лишние запросы через релей).
3. **Нет лимита на количество сохранённых поисков и мониторов на пользователя.** Один юзер
   теоретически может занять слоты монитора; справедливая очередь (`DISTINCT ON user_id`) —
   следующая итерация.
4. **Залог и комиссия теперь «мягкие» при неопределённости.** Если в объявлении про залог
   или комиссию ничего не сказано, оно показывается с пометкой «❔ не указано», а
   отбраковывается только явное «депозит 50 000» / «комиссия 50%». Раньше неизвестность
   считалась отказом, и запросы «без залога» / «без комиссии» гарантированно отдавали пустую
   страницу. Если нужна прежняя строгость — это одна строка в `matcher._check_deposit`.
5. **JSON-колонки вместо JSONB.** Работает, но без индексов и операторов `@>`.
6. **mypy: ~108 замечаний** (в основном `Message | InaccessibleMessage | None` в хендлерах
   aiogram). Риск закрыт глобальным обработчиком ошибок (`app/bot/errors.py`), но типы стоит
   подчистить: это следующий технический долг.
7. **`OPENROUTER_API_KEY` лежит в `.env`, но `LLM_PROVIDER=rule`.** Ключ не используется;
   либо включить LLM-режим, либо убрать ключ из окружения.
8. **Репозиторий на GitHub недоступен** (аккаунт заблокирован), поэтому авто-обновление через
   `origin` стоит вхолостую. Как только появится приватный репо: `git remote set-url origin`,
   `git push -u origin main` — и `kvartira-autopull.timer` начнёт катить коммиты сам.

## 6. Переменные окружения, которые появились

| Переменная | Смысл | По умолчанию |
|---|---|---|
| `KRISHA_FETCH_RELAY` | URL релея для обхода блокировки датацентровых IP | пусто (прямые запросы) |
| `KRISHA_RELAY_TOKEN` | ключ релея, если есть | пусто |
| `KRISHA_RELAY_MIN_INTERVAL_SECONDS` | пауза между запросами через релей | 3.2 (на проде 4.0) |
| `KRISHA_RELAY_ENGINE` | `direct` / пусто (headless) | пусто |
| `LISTING_ALLOWED_HOSTS` | домены, которым можно быть кнопкой «открыть объявление» | `krisha.kz` |
| `KRISHA_MAX_PAGES`, `KRISHA_MAX_DETAILS` | бюджет запросов на один поиск | 5 / 40 (на проде 1 / 5) |
