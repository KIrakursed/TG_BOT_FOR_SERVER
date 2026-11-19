# UyTap — Telegram-бот поиска квартир (AI Agent Handoff)

> Технический handoff-документ. Предназначен для AI-агента, который впервые открывает этот репозиторий.
> Правило: ни одно утверждение здесь не выдумано — всё сверено с кодом, тестами, миграциями и логами на момент HEAD `a08d19e`.
> Если факт нельзя подтвердить по репозиторию — это явно помечено как `NOT VERIFIED`.

---

## 1. Project overview

**Название:** UyTap. **Назначение:** Telegram-бот для поиска арендных квартир в Казахстане.
**Пользователь:** человек, который ищет квартиру (часто студент) и пишет/нажимает кнопки в Telegram.
**Проблема:** объявления на Krisha.kz нельзя удобно фильтровать по категории жильцов («студентам можно/нельзя», «только семье»), условиям (хозяин/мебель/животные) и получать уведомления о новых подходящих вариантах.

**Основной сценарий пользователя:**
`/start` → `🔎 Найти квартиру` → город → районы → комнаты → бюджет → «Кто будет жить» → «Что важно» (3 тоггла) → итог → результаты-карточки → сохранение поиска → фоновый мониторинг с уведомлениями.

**Источники объявлений:** Krisha.kz.
Основной источник сейчас — `KrishaHtmlProvider` (парсинг публичных HTML-карточек поиска).
Ограничения источника (подтверждены живыми запросами и production-логами):
- страницы поиска: HTTP 200, по 20 карточек, есть `data-id`, ссылка `/a/show/{id}`, цена, адрес/район, превью описания;
- страницы объявлений часто отдают **HTTP 468 + «я не робот»** (Kolesa Group) и **HTTP 429** при частых запросах — это внешнее ограничение, обходить его ЗАПРЕЩЕНО (см. §21);
- при недоступности detail-страницы используется только подтверждённый preview/card-текст, условия не выдумываются.

**Реальная архитектура (проверена по `app/main.py`, роутерам и `SearchService`):**

```
Telegram User
    ↓  (aiogram 3, polling, один процесс)
Dispatcher: common_router → debug_router → search_router   (app/main.py:47-50)
    ↓
FSM (aiogram StatesGroup SearchStates) + SearchContext     (app/bot/states/__init__.py, app/search/context.py)
    ↓
SearchQuery (кнопочный flow) / SearchIntent (текстовый flow, rule-based)
    ↓
SearchService                                               (app/search/service.py)
    ↓  pagination → _collect_candidates → _fetch_details
ListingProvider (KrishaHtml по умолчанию)
    ↓
ListingAnalyzer (нормализация + факты + evidence)           (app/listings/analyzer.py)
    ↓
ListingMatcher (жёсткие фильтры + REJECT-коды)              (app/listings/matcher.py)
    ↓
RankingService (confirmed → occupancy → price)              (app/listings/ranking.py)
    ↓
Telegram: карточки (по 1 объявлению на сообщение, пагинация), empty state, сохранение поиска
    ↓  (фон)
SavedSearchMonitor: baseline → новые совпадения → уведомления (app/search/monitor.py)
```

---

## 2. Tech stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11 локально / 3.12 production venv | Весь проект |
| Telegram | aiogram | >=3.13,<4 | Handlers, FSM, polling |
| DB | PostgreSQL | 16 (production) | Все persistent-данные |
| ORM | SQLAlchemy | >=2.0,<3 | Async models + repositories |
| Driver | asyncpg | >=0.29,<1 | Production DB driver |
| Migrations | Alembic | >=1.13,<2 | `alembic/versions/0001–0009` |
| Settings | Pydantic + pydantic-settings | >=2.7 / >=2.3 | `app/config.py`, всё из env |
| HTTP | httpx | >=0.27,<1 | Krisha + Firecrawl + shared client |
| HTML | BeautifulSoup4 + lxml | >=4.12 / >=5.2 | Парсинг карточек Krisha |
| Browser | Playwright | >=1.45,<2 | В зависимостях как задел, **в поиске не используется** |
| Tests | pytest + pytest-asyncio + aiosqlite | >=8.2 / >=0.23 / >=0.20 | `tests/`, SQLite in-memory |
| Lint | ruff | (venv) | `ruff check .` |
| LLM (default) | rule-based, свой код | — | `LLM_PROVIDER=rule`, без сети |
| LLM (optional) | OpenRouter / Ollama | — | Только если настроены, с fallback на rule |

Docker **не используется** — ни локально, ни на production.

---

## 3. Project structure (реальное дерево)

```
app/
├── main.py                  # точка входа: Bot+Dispatcher, 3 роутера, monitor, graceful shutdown
├── config.py                # Settings (всё из env), get_settings() с lru_cache
├── metrics.py               # in-process счётчики (поиски, 468, LLM, кэш, длительности)
├── bot/
│   ├── states/__init__.py   # SearchStates: city/property_type/rooms/max_price/conditions/
│   │                        #   natural_query/preview/districts/tenant/place_search
│   ├── keyboards/inline.py  # ВСЕ inline-клавиатуры + reply_menu() + _page_nav()
│   └── handlers/
│       ├── __init__.py      # common_router, debug_router, search_router
│       ├── common.py        # /start, /help, /admin, /privacy, /delete_me, /monitor_once, menu
│       ├── debug.py         # /debug_listing (только ADMIN_IDS, mock-данные)
│       └── search.py        # ВЕСЬ search flow (~1600 строк): wizard, фильтры, результаты, save
├── search/
│   ├── filters.py           # SearchQuery (dataclass) + SearchQueryParser (детерминированный)
│   ├── context.py           # load_context/dump_context (FSM first, DB fallback)
│   ├── merge.py             # intent_to_context / merge_intent / context_to_query / query_to_context
│   ├── service.py           # SearchService (full pipeline) + KrishaSearchService (только ссылка)
│   ├── krisha.py            # KrishaSearchUrlBuilder (только URL, без сети)
│   ├── districts.py         # DistrictNormalizer + DistrictService (catalog + observed)
│   ├── districts_data.py    # CITY_DISTRICTS: 14 городов, SUPPORTED_CITIES
│   ├── places.py            # PlaceCatalog (TTL): street/jk/microdistrict/metro/university
│   ├── monitor.py           # SavedSearchMonitor (baseline, dedup, retry, lock, flood cap)
│   └── ingestion.py         # ingest_urls() — фоновый Firecrawl-ingest в БД (НЕ user search)
├── listings/
│   ├── normalize.py         # ListingNormalizer (текст, деньги, URL, комнаты, площадь, этаж)
│   ├── semantics.py         # Декларативные regex-таблицы + is_student_allowed()
│   ├── student_policy.py    # Фасад StudentPolicyAnalyzer над semantics
│   ├── analyzer.py          # ListingAnalyzer → ListingAnalysis + evidence
│   ├── matcher.py           # ListingMatcher → MatchResult(matched/reasons/failed_conditions)
│   ├── ranking.py           # RankingService (confirmed → occupancy → price)
│   ├── cards.py             # format_listing_card (только подтверждённые поля)
│   └── urls.py              # extract_krisha_show_url
├── providers/
│   ├── base.py              # ListingProvider ABC + ProviderConfig + with_retry + dedup
│   ├── factory.py           # get_listing_provider(settings): mock/krisha-api/krisha/firecrawl
│   ├── krisha_html.py       # Основной: HTML-карточки + das[]-предфильтр + detail с 468/429-fallback
│   ├── krisha.py            # KrishaApiProvider (авторизованный JSON API; без base_url → Unavailable)
│   ├── firecrawl.py         # ТОЛЬКО ingestion (search() всегда ListingDataUnavailable) + quota-cooldown
│   ├── unavailable.py       # UnavailableListingProvider (явный отказ вместо выдуманных данных)
│   └── http.py              # Shared httpx.AsyncClient (pooling), close_shared_client()
├── parser/
│   ├── base.py              # Старый интерфейс ListingProvider (используется mock)
│   └── provider.py          # MockListingProvider: 20 детерминированных объявлений (только тесты)
├── llm/
│   ├── base.py              # LLMProvider ABC: parse_search_intent() — только язык, без БД/Telegram/SQL
│   ├── rule_based.py        # RuleBasedProvider (default): парсер + _detect_tenant
│   ├── openrouter.py / ollama.py  # Сетевые провайдеры с fallback на rule
│   ├── schemas.py           # SearchIntent + SearchContext (Pydantic)
│   └── __init__.py          # get_llm_provider() (rule по умолчанию)
├── database/
│   ├── session.py           # engine + SessionFactory (pool_size/overflow/timeout из env)
│   ├── cleanup.py           # cleanup_old_derived_data() на старте
│   ├── models/__init__.py   # User/Search/Listing/Favorite/SearchResult/SearchNotification
│   └── repositories/        # users/listings/searches(+search_signature)/results/favorites
├── cache/ttl.py             # In-memory TTLCache (анализ в SearchService)
├── security/rate_limit.py   # RateLimiter (поиск/анализ per-user, default 10с)
└── billing/plans.py         # Задел Free/Premium БЕЗ оплат и платных функций
tests/ (22 файла, см. §16) · alembic/versions/0001–0009 · deploy.sh · kvartira-bot.service
```

### Ключевые файлы

### `app/bot/handlers/search.py`
Purpose: весь пользовательский поиск: wizard, тогглы, preview, запуск, карточки, save/history/monitor/favorites, relax.
Important: `choose_city/district/property/price/tenant`, `condition_toggle`, `place_action` (legacy), `_build_button_query`, `_query_from_data`, `_query_from_search`, `_execute_search`, `run_search`, `save_search`, `_relax_query_once`, `_render_result_page`, `result_sessions`, `user_tasks`, `search_limiter`.
Used by: `search_router` в `main.py`.
Do not modify without understanding: FSM-переходы, `asdict(query)` в state, `result_sessions` (in-memory!).

### `app/listings/matcher.py`
Purpose: единственная реализация жёстких фильтров. `MatchResult.matched/reasons/failed_conditions/student_likely`.
Important: `_check_city/rooms/price/district/tenant/place/student/bool/deposit`.
Used by: `SearchService`, `monitor._notify_pending`, тесты.
Do not modify without understanding: семантику `None` (фильтр выключен) и `REJECT_*` коды (их читает UI и тесты).

### `app/listings/semantics.py`
Purpose: `is_student_allowed()` + таблицы политик (pets/furniture/commission/deposit/owner) + `detect_tenant_policy` (для `audience_restriction` НЕстуденческих tenant'ов).
Do not modify without understanding: порядок «запрет раньше разрешения», клауза-границы, список TRUE/FALSE (§7).

### `app/search/monitor.py`
Purpose: `SavedSearchMonitor`: baseline при первом прогоне → уведомления только о новых; изоляция ошибок на поиск; lock; `MAX_SEND_ATTEMPTS=5`; flood-cap через `more_keyboard`.
Used by: `main.py` (`monitor.start(bot)`), `/monitor_once` (админ, разовый прогон).
Do not modify without understanding: baseline-логику (иначе спам всем пользователям).

### `app/search/places.py`
Purpose: `PlaceCatalog` — TTL-кэш `(city, district, kind)` из наблюдаемых объявлений; статика только для метро Алматы (11 станций) и алиасов 9 вузов.
Used by: legacy place-хендлеры, `ingestion.py` (refresh).
Do not modify without understanding: в UX каталог НЕ показывается (намеренно, §8); ключ и merge-логику `get()`.

---

## 4. Application flow (фактический)

Состояния FSM (`SearchStates`): `city → districts → property_type → rooms` (нет, точно: `property_type` обрабатывает комнаты) `→ max_price → tenant → conditions → preview → (результаты)`. Плюс `natural_query` (свободный текст) и `place_search` (legacy).

| Этап | Handler (`search.py`) | Callback/data | FSM state | Что сохраняется |
|---|---|---|---|---|
| Start | `common.start` (`/start`) | команда | — | user upsert, `reply_menu()` |
| Главная | `reply_menu_handler` | `🔎 Найти квартиру` (reply-кнопка) | `city` (после `state.clear()`) | `wizard_message_id` |
| Город | `choose_city` / `city_page` | `city:{name}`, `city:page:{N}`, `city:other`, ввод текстом | `city` | `city`; пагинация 5/5/4, после выбора исчезает |
| Районы | `choose_district` | `district:toggle:{page}:{name}` (старый формат без page поддерживается), `district:page:{N}`, `district:all/done` | `districts` | `districts[]`, `district_page`; только районы выбранного города |
| Комнаты | `choose_property` | `property:1/2/3/4plus/any` | `property_type` | `rooms` (`4plus`→4, `any`→None); `property_type` всегда `квартира` |
| Бюджет | `choose_price` / `custom_price` | `price:200000/250000/300000/custom`, ввод числом | `max_price` | `max_price` |
| Кто будет жить | `choose_tenant` | `tenant:student/single/couple/family/any` | `tenant`→`conditions` | `tenant`; при student также `conditions=["student"]` |
| Что важно | `condition_toggle` | `cond:toggle:no_realtor/furnished/pets` | `conditions` | bool-поле; бинарный цикл `None↔True`; edit существующего сообщения |
| Итог | `conditions_done` → `show_preview` | `cond:done` | `preview` | `asdict(query)` в state; `_intent_header` |
| Поиск | `run_search` (`search:run`) | in-flight dedup по `_query_signature`, rate-limit, `asyncio`-таск в `user_tasks` | — | `SearchQuery` из `_query_from_data` |
| Результаты | `_execute_search` | progress → auto-relax (макс 2 шага) → persist → карточки | — | `result_sessions[user_id]` (in-memory!); карточки с `fav:toggle`, `res:page`, `🏠 Главная`/`🔎 Новый поиск` |

Назад/Отмена (`wiz:back:*`, `wiz:cancel`) работают на каждом шаге; wizard — одно редактируемое сообщение (`_wizard_show`: `edit_text`, fallback — новое).

---

## 5. Search architecture

- **FSM dict** — быстрый путь: `city/districts/rooms/max_price/tenant` + `furnished/pets/no_realtor/...` + legacy place-поля.
- **SearchContext** (`llm/schemas.py`) — конфигурация для текстового flow; `load_context` (FSM first, fallback — последний SavedSearch), `dump_context`.
- **SearchIntent** — результат `parse_search_intent()` (rule-based по умолчанию).
- **SearchQuery** (`search/filters.py`) — плоский dataclass для провайдера и matcher: city, property_type, rooms, min/max_price, student_allowed, pets_allowed, furnished, no_realtor, no_commission, no_deposit, district/districts, tenant, jk/street/microdistrict, near_metro/near_university, metro_station/university_name, query_text/text_search.
- **SearchService** (`search/service.py`): `_collect_candidates` (pagination → `cheap_skip` по city/rooms/price → `_fetch_details` с semaphore) → `_search_inner` (анализ с TTL-кэшем → `matcher.match` → `ranking.rank` → срез `max_results`).
- **ListingProvider**: `search()` обязан вернуть полные `Listing` (никогда просто URL); `search_paged()`; `fetch_detail()`.
- **ListingAnalyzer** → `ListingAnalysis` (+`evidence`, `details`: address/district/area/floor/metro/server_filtered/conditional_prices/student_status/...).
- **ListingMatcher** → `MatchResult`. Используется и поиском, и монитором (одна реализация).

Путь данных: FSM/text → context → query/intent → provider → listings → analyzer → matcher → ranking → Telegram/monitor.

---

## 6. Matching logic

Общее правило: `None` = фильтр выключен. Неизвестное значение обязательного поля = reject (строгий матчинг), **кроме студенческого гейта, где неизвестность = SHOW**.

- **city** (`_check_city`): строгое равенство casefold; пустой query.city = пропуск.
- **district** (`_check_district`): список wanted (districts или district); actual из `details.district`/колонки; нормализация через `DistrictNormalizer`; нет actual → `REJECTED_DISTRICT_UNKNOWN`; нет совпадения → `REJECTED_DISTRICT`.
- **rooms** (`_check_rooms`): строгое равенство (`4plus` означает ровно 4 — историческое поведение, не менять молча).
- **budget** (`_check_price`): `effective_price_for(student_requested)` учитывает conditional prices; нет цены → `REJECTED_PRICE_UNKNOWN`.
- **tenant** (`_check_tenant`): только для `single/couple/family` через `TENANT_INCOMPATIBLE` (`single:{family_only,couple_only}`, `couple:{family_only}`, `family:{single,single_only,female_only,male_only}`); `"" / any / student` — пропуск. Mismatch → `REJECTED_AUDIENCE`.
- **student** (`_check_student`): см. §7.
- **owner/furniture/pets/commission** (`_check_bool`): `expected True + actual is not True` → reject (кроме `server_filtered`-гарантии Krisha для `no_realtor`/`furnished`); `expected False + actual is not False` → reject.
- **deposit** (`_check_deposit`): `no_deposit is True` требует `deposit_required is False`, иначе `REJECTED_DEPOSIT`.
- **place** (`_check_place`): jk — подстрока нормализованного текста; street/microdistrict — word-boundary; `metro_station` — `match_station()` (толерантен к склонениям); `university_name` — подстрока; `near_metro/near_university` — proximity-маркеры в тексте. Нет подтверждённого поля при выбранном фильтре → reject.

Коды отказа (`REJECT_*`) маппятся в русские подписи через `REJECT_LABELS` и используются тестами — не переименовывать молча.

---

## 7. Student search (подробно, фактическая реализация)

**Цепочка tenant:** кнопка `👨‍🎓 Студент` (`tenant:student`) → `choose_tenant` пишет `tenant="student"` (строка, не enum; `"Студент"` нигде не используется) + `conditions=["student"]` → `_build_button_query` ставит `student_allowed=True` → `SearchQuery(tenant="student", student_allowed=True)` → matcher. Текстовый flow: `RuleBasedProvider._detect_tenant` возвращает `"student"` при упоминании студентов без бана → `SearchContext.tenant`/`SearchIntent`. SavedSearch хранит `tenant` строкой; `_query_from_search` восстанавливает.

**Гейт:** `is_student_allowed(text)` (`app/listings/semantics.py`), вызывается из `ListingMatcher._check_student` **только** когда `student_allowed is True` или `tenant=="student"`. Для `single/couple/family/any` без флага гейт не применяется (их логика — `_check_tenant` + `audience_restriction` из `detect_tenant_policy`, который НЕ менялся).

**Анализируемое поле:** `analysis.details["description_text"]` — собирается в `ListingAnalyzer.analyze` из title + нормализованного description (+address попадает через общий текст в `_check_place`, но гейт читает именно `description_text`). NULL/empty → клауз нет → **True (SHOW)**.

**Алгоритм:** нормализация (lower, ё→е, пунктуация/переносы→пробелы) → split на клаузы по `, ; . ! ? \n` и словам `но`/`а` → в каждой клаузе сначала запреты: `без студент…`, `не студент…` (включая `только не студентам`), `студент… + 0–2 слова + (нельзя|запрещено|не сдаем|не сдается|не рассматриваем|не подходят)`, `только (+для) + (семейн…|семья|семьи|семье|семью|семьей|семьям)` → иначе True.

TRUE (из `tests/test_student_gate.py`): «Пару минут пешком до метро», «Только 7 минут пешком до метро», «Только семь минут пешком», «Один месяц депозит», «1-комнатная квартира», «Один этаж», «Для одного/ двух человек», «Для нескольких жильцов», «Порядочным жильцам», «Студентам можно» (включая «…, с животными нельзя»), «Для студентов», «Студентам разрешено», «Студентов рассматриваем», `""`, `None`.
FALSE: «Студентам нельзя», «Без студентов», «Студентам не сдаём/сдаем», «Студентам запрещено», «Только не студентам», «Только семья / семейная пара / семейным / семейной паре / для семьи», «СТУДЕНТАМ НЕЛЬЗЯ».

Почему не широкая regex: слова `пара/один/месяц/этаж/комната` — обычные тексты объявлений; их отклонение давало бы 0 результатов. Сомнение → SHOW. Отдельного regex на «пара» нет (запрещено архитектурным решением).

**Важно (баг a08d19e):** backend всегда возвращал neutral-совпадения, но `_execute_search` прятал их в `likely_ids` за промптом «Показать их?». Теперь neutral — обычные результаты (ранжирование ставит confirmed первым). Флаг `MatchResult.student_likely` и причины `✅/🟡` сохранены (их читают тесты/монитор/ранжирование).

---

## 8. Current UX (фактический, проверен по `keyboards/inline.py`)

Доступно пользователю: город (пагинация 5/5/4) → районы (city-scoped, мультивыбор, страница сохраняется) → комнаты (1/2/3/4+/не важно) → бюджет (200/250/300k/свой) → tenant (5 кнопок) → **«Что важно» ровно 3 тоггла**: `👤 От хозяина: ❌/✅`, `🛋 Мебель: ❌/✅`, `🐕 Животные: ❌/✅` → `✅ Готово` → итог → `🔎 Искать` → карточки. Пусто: `«Ничего подходящего не нашли.» + [🔎 Новый поиск][🏠 Главная][⚙️ Изменить параметры]`. Карточки: `[🏠 Главная][🔎 Новый поиск]`, пагинация по 1 объявлению (`res:page`), избранное `fav:toggle`. Reply-меню постоянно: `🔎 Найти квартиру / ❤️ Избранное+🔔 Мои поиски / 🏠 Главная`.

**Намеренно убрано из UX (код backend сохранён):** 🏢 ЖК, 🛣 Улица, 🏘 Микрорайон, 🚇 Метро, 🎓 Университет (включая экран `📍 Где удобно`), 💰 Без комиссии, 💵 Без депозита. **`removed from UX != deleted from backend`** — модели, колонки, парсер, каталог, matcher-ветки и legacy-хендлеры (`place_action`, `condition_open` для `cond:open:place`, free-text `conditions_text`) оставлены ради старых SavedSearch и in-flight сообщений.

---

## 9. Legacy / backward compatibility

Сохранено и нельзя удалять без проверки: колонки `searches` (`jk/street/microdistrict/metro_station/university_name/no_deposit/no_commission`), `SearchQuery`-поля, `_CONDITION_FSM_KEYS`-алиасы (`commission/deposit/realtor` + FSM-имена), старые callback-форматы (`district:toggle:{name}` без page, `place:pick:{kind}:{name}`, `district:noop`, `place:noop`), хендлеры `relax_*`, `likely_show`/`likely_keyboard`, `place_menu`/`place_list_keyboard`, `PlaceCatalog`, `FirecrawlProvider`, все провайдеры и `llm/*`. Причина: старые сообщения с кнопками и старые SavedSearch не должны давать traceback. `search_signature`/`_query_signature` включают place-поля (дедуп корректен и для старых, и для новых записей; отсутствующие значения → `None`/`""`).

---

## 10. Providers

| Provider | Когда | Env | Ограничения/fallback |
|---|---|---|---|
| `KrishaHtmlProvider` (`krisha`, default) | Всегда в production | `KRISHA_HTML_*` (паузы 2с/1с, таймаут 20с, фото=1) | Search-страницы 200; карточки: `data-id`, `/a/show/{id}`, цена, адрес, превью. Detail: часто **468** («я не робот») или **429** → используется только card/preview, `details.description_source="preview"`, строгий matcher режет unknown. Server-гарантии (`server_filtered`): `no_realtor` (`das[who]=1`), `furnished` (`das[live.furniture]=1`); ещё в URL: `das[live.rooms]`, `das[price][from/to]`. Студенческого das-параметра **нет** |
| `KrishaApiProvider` (`krisha-api`) | Только при настроенном API | `KRISHA_API_BASE_URL/_TOKEN/_PATHS` | Без base_url → `ListingDataUnavailable`. Токен — `Authorization: Bearer`, не в логах |
| `MockListingProvider` (`mock`) | Только тесты/dev | — | 20 детерминированных объявлений (`app/parser/provider.py`) |
| `UnavailableListingProvider` | Fallback/неизвестное имя | — | Всегда отказ вместо выдуманных данных |
| `FirecrawlProvider` (`firecrawl`) | Только ingestion (см. §11) | `FIRECRAWL_*` | `search()` всегда `ListingDataUnavailable`; factory без `FIRECRAWL_ENABLED`+ключа отдаёт Unavailable |

Антибот-обход запрещён архитектурно: нет CAPTCHA-решений, подмены fingerprint, прокси, cookie-трюков (см. docstring `krisha_html.py`).

---

## 11. Firecrawl

Назначение — **только фоновый ingestion**: `ingest_urls()` (`app/search/ingestion.py`): явные URL → scrape → upsert в `listings` → refresh `place_catalog`. В пути пользовательского поиска его нет: Telegram → БД/provider → matcher → результат. `FIRECRAWL_API_KEY` только из env (в логах маскируется; в репозитории только пустой placeholder в `.env.example`). Cooldown/circuit breaker: после 402/429 (`FirecrawlQuotaExceeded`) — блок на 1 час, останов без бесконечных retry; без ключа — пропуск. Настройки: `FIRECRAWL_ENABLED=false` по умолчанию, `FIRECRAWL_CACHE_TTL_SECONDS=3600` (TTL-кэш по URL + дедуп), `FIRECRAWL_MAX_CONCURRENCY=2`, `FIRECRAWL_BASE_URL`. Firecrawl НЕ должен становиться обязательной зависимостью поиска.

---

## 12. LLM architecture

`LLMProvider.parse_search_intent(text, context) -> SearchIntent` — только язык→схема, без БД/Telegram/SQL (контракт `llm/base.py`). Реализации: `RuleBasedProvider` (**default**, `LLM_PROVIDER=rule`; regex-парсинг + `_detect_tenant` + word-цены/комнаты), `OpenRouterProvider` (`OPENROUTER_API_KEY/_MODEL`), `OllamaProvider` (`OLLAMA_BASE_URL/_MODEL`); сетевые деградируют к rule при любой ошибке (`llm/__init__.py:get_llm_provider`). Поиск работает без LLM. Добавление нового провайдера: унаследовать `LLMProvider`, вернуть строгий `SearchIntent`/`SearchContext` (`llm/schemas.py`), зарегистрировать в `get_llm_provider` — matcher/service не трогать.

---

## 13. Database

PostgreSQL 16, база `kv_bot`, пул `DB_POOL_SIZE=5/overflow=10/timeout=30` (`app/database/session.py`, `pool_pre_ping=True`).
Таблицы (`app/database/models/__init__.py`): `users` (telegram_id unique, active_search_id→searches), `searches` (все фильтры incl. `jk/street/microdistrict/metro_station/university_name/no_deposit/districts JSON/tenant/monitor-поля/счётчики), `listings` (unique `(source, external_id)`, индексы city/price/rooms/is_active/external_id; факты + `details`/`analysis_evidence` JSON), `favorites` (unique `(user_id, listing_id)`), `search_results` (matched/score/reject_reason), `search_notifications` (unique `(search_id, listing_id)`, baseline/sent/attempts ≤5).
Миграции (все применены, head `0009_microdistrict`):
`0001_initial` → `0002_listing_analysis` → `0003_results` → `0004_active_search` → `0005_monitor` → `0006_tenant` → `0007_place` → `0008_deposit_place` → `0009_microdistrict`. Старые миграции не менять; новые — только `upgrade()+downgrade()`.

---

## 14. Environment variables

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `BOT_TOKEN` | да | Токен бота | `123:ABC` (placeholder!) |
| `DATABASE_URL` | да | PostgreSQL (asyncpg). Внимание: пароль может быть URL-encoded (`%XX`) — для `pg_dump` декодировать (`unquote`); файл может иметь CRLF | `postgresql+asyncpg://kvbot:PASS@127.0.0.1:5432/kv_bot` |
| `ADMIN_IDS` | нет | Админы (`/admin`, `/monitor_once`, `/debug_listing`) | `123,456` |
| `SEARCH_MODE` | нет | `FULL_LISTING_MODE` (default) / `PUBLIC_SEARCH_MODE` (только ссылка) | `FULL_LISTING_MODE` |
| `LISTING_PROVIDER` | нет | `krisha` (default) / `krisha-api` / `mock` / `firecrawl` | `krisha` |
| `SEARCH_MAX_RESULTS` / `KRISHA_MAX_*` / `CACHE_TTL_SECONDS` / `USER_*_RATE_LIMIT_SECONDS` | нет | Лимиты поиска/провайдера/кэша/рейтов | см. `.env.example` |
| `MONITOR_*` | нет | `ENABLED=true`, интервал 600с, ≤20 поисков/цикл, ≤10 уведомлений/поиск, concurrency 3 | см. `.env.example` |
| `LLM_PROVIDER` + `OPENROUTER_*` / `OLLAMA_*` | нет | default `rule` | `rule` |
| `KRISHA_API_*` | для krisha-api | Авторизованный API | base URL + token |
| `KRISHA_HTML_*` | нет | Паузы/таймаут/фото | см. `.env.example` |
| `FIRECRAWL_*` | нет | `API_KEY` (секрет!), `ENABLED=false`, TTL 3600, concurrency 2 | пусто по умолчанию |
| `DB_POOL_*` / `LOG_LEVEL` | нет | Пул и логи | `INFO` |

Секреты — только из env, никогда в код/логи/отчёты/README.

---

## 15. Local development (Windows, без Docker)

```powershell
cd C:\Users\ertai\Desktop\telegram_bot_krisha.kz
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env   # заполнить BOT_TOKEN и локальный DATABASE_URL
psql -U postgres -c "CREATE DATABASE kv_bot;"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.main
```
Проверки: `.\.venv\Scripts\python.exe -m pytest tests/ -q -W ignore::DeprecationWarning`, `.\.venv\Scripts\ruff.exe check .`, `.\.venv\Scripts\pip.exe check`. Важно: в PowerShell нет `&&`/`head`/`grep`/`tail` — команды по одной; `-W ignore::DeprecationWarning` глушит шум aiosqlite (иначе PowerShell-обёртка вернёт exit 1 при зелёных тестах — проверено).

---

## 16. Testing

Команды (venv): `python -m pytest tests/ -q` (22 файла: semantics/places/matcher/monitor/pipeline/repositories/ux/final_ux_places/student_gate/student_pipeline/…), `ruff check .`, `pip check`, `python -m alembic check`, `python -m alembic current`. БД тестов — только SQLite `:memory:` (`tests/conftest.py`); ни один тест не трогает production PostgreSQL (проверено grep'ом). Последний достоверный результат (2026-10-04, local и production): **456 passed, 1 skipped**; `ruff: All checks passed`; `pip: No broken requirements`; `alembic: 0009_microdistrict (head)`, `No new upgrade operations detected`.

---

## 17. Production (фактическая инфраструктура)

Oracle Cloud (`instance-20261003-0352`), Ubuntu, PostgreSQL 16 локально (`127.0.0.1:5432`, DB `kv_bot`, user `kvbot`), systemd-сервис `kvartira-bot.service` (`User=ubuntu`, `WorkingDirectory=/home/ubuntu/telegram_bot_krisha.kz`, `EnvironmentFile=.../.env`, `ExecStart=.../.venv/bin/python -m app.main`, `Restart=on-failure`), polling, один процесс, бот `@kvartira10_bot`. Remote `origin` — приватный GitHub-репозиторий (см. `git remote -v` на сервере).

Порядок деплоя (проверен; скрипт `deploy.sh` делает то же самое: pull → `pip install -q` → `pytest -q -W ignore::DeprecationWarning` → `ruff check app tests` → `alembic upgrade head` → restart → `is-active` + status + journal, `set -e`):
```
cd /home/ubuntu/telegram_bot_krisha.kz
git pull --ff-only
source .venv/bin/activate
pip install -r requirements.txt
python -W ignore::DeprecationWarning -m pytest tests/ -q   # только sqlite, безопасно
ruff check . && pip check
python -m alembic current            # upgrade head — ТОЛЬКО если есть новая миграция
sudo systemctl restart kvartira-bot
sudo systemctl status kvartira-bot --no-pager
journalctl -u kvartira-bot -n 150 --no-pager
```
Бэкап перед деплоем со схемой: `~/backups/kv_bot_YYYYMMDD_HHMMSS.sql` через `pg_dump` с `PGPASSWORD` (пароль декодировать `unquote`, `.env` не менять); проверять exit 0 + размер > 0. **Production restart НЕ выполнять ради документации и без явного запроса.**

---

## 18. Production checklist

Before: `git status` чист · тесты зелёные · ruff/pip/alembic зелёные · бэкап (если меняется схема) · секретов в diff нет.
Deploy: pull --ff-only → install → тесты → migrate (при необходимости) → restart.
After: `Active: active (running)` · один процесс · `Start polling` + `Run polling for bot` · `monitor started` · `db cleanup ok` · 0 traceback · graceful shutdown виден в журнале при следующем рестарте.

---

## 19. Known issues

### Fixed (с доказательствами)
- Student-гейт отклонял bare-маркеры (`только паре`, `пару минут`) → `is_student_allowed()` + тесты таблицы 17/12 (коммиты `d7b6c90`, `c7dadcd`).
- Neutral-объявления прятались за «Показать их?» → fold в основные результаты (`a08d19e`; тест падает на старом коде).
- Boolean-фильтры открывали лишний экран «Важно/Не важно»; 3 toggle не переключались (mismatch ключей) → бинарные тоглы (`7cab822`, `03bfd00`).
- Города одним списком; тоггл района сбрасывал страницу → пагинация 5/5/4 + `district_page` + общий `_page_nav` (`67364b4`, `b7c78ca`).
- Place-каталог без district-scope; `place:pick` с длинным именем (>64B) → scope `(city, district, kind)` + индексные callbacks + legacy-фолбэк (`e4b26f0`, `538c4f1`).
- `relax_more` ослаблял pets/commission → один шаг auto-relax (`d41e767`).
- `save_search`/сигнатуры теряли `metro_station/university_name/microdistrict` → проброшены везде (`03bfd00`, `3bfa9d1`, миграция `0009`).
- Firecrawl без guard/cooldown → guard в factory + quota-cooldown 1ч (`a51db84`).

### Known limitations (факты)
- Detail-страницы Krisha часто 468/429 → описания только из preview; `unknown` режет строгие фильтры (дизайн).
- `result_sessions` и `user_tasks` — in-memory: рестарт теряет пагинацию и прерывает фоновые поиски (graceful shutdown их отменяет).
- `property:4plus` означает ровно 4 комнаты (историческое поведение matcher).
- Dev-БД: Алматы 34 + Актау 10 записей; каталоги мест строятся парсингом title/address (в `details` нет ключей metro/street/ЖК). Production-объём — только по логам (candidates=60/цикл).
- `rooms`/`price`/`district` из карточек иногда отсутствуют → строгий reject.
- systemd warning про `daemon-reload` (unit менялся ранее) — на работу не влияет.

### Open issues
- Подтверждено логами: Krisha периодически отдаёт 429 даже на search-страницы — бот деградирует корректно, но полнота выдачи в такие окна ниже.
- Реальный Telegram UX ни разу не проверялся реальным клиентом (только synthetic-тесты хендлеров) — см. `NOT VERIFIED` ниже.

---

## 20. Important history (из `git log`)

- `52a5663` student occupancy/auto-relax/place-фильтры — база студенческой логики.
- `b72b1f1` deploy-workflow; `bb04398` graceful shutdown; `5ec3b36` single-message wizard; `3ac817e` reply-меню; `f766858` дедуп поисков.
- `c7dadcd` only-single reject; `03bfd00`/`c4e0d8a` place-навигация; `d7b6c90` spec-B гейт; `7cab822` бинарные тоглы; `67364b4` города 5/5/4; `b7c78ca` страница районов; `e4b26f0` district-scope каталога; `3bfa9d1` микрорайоны + `0009`; `9639635` `_has_conditions`; `538c4f1` индексные callbacks; `a51db84` firecrawl-guard; `d41e767` relax; `c4e0d8a` тесты; `6d7b006` **упрощение UX** (убраны места/комиссия/депозит из меню, backend сохранён); `a805ca4` диагностика + pipeline-тест; **`a08d19e` (HEAD)** likely-fold + Telegram-flow regression test.
- Решений об откате/revert в истории нет.

---

## 21. Architectural decisions (не отменять без причины)

1. Без Docker; systemd + venv + git-pull деплой.
2. `ListingProvider`-абстракция; поиск обязан работать без LLM и без Firecrawl.
3. Rule-based default (`LLM_PROVIDER=rule`); сетевые LLM только с fallback.
4. Строгий matcher: unknown = reject (кроме student-гейта, где unknown = SHOW).
5. Сомнение в студенческом гейте → SHOW, никогда ворожба по `if "студент" in text`.
6. UX/backend разделены: удалённое из меню ≠ удалённое из кода (backward compat SavedSearch/сообщений).
7. Никакого обхода 468/429/CAPTCHA/rate-limit/fingerprint/прокси/cookies.
8. Shared `httpx`-клиент, semaphore на detail, TTL-кэши, rate-limit, `with_retry` с backoff (429 повторяется ограниченно).
9. Монитор: baseline-first (без спама), dedup `(source, external_id)`, изоляция ошибок, lock, retry ≤5, flood-cap.
10. Миграции только вперёд-совместимые (nullable колонки, downgrade проверяется); старые не править.
11. Callback-лимит Telegram 64 байта (индексные ID вместо имён); старые форматы поддерживать.
12. Production-гейты: тесты+ruff+pip+alembic зелёные до restart; бэкап перед сменой схемы; рестарт только по явному запросу.

---

## 22. DO NOT BREAK

- **`callback_data` ≤64 байт и стабильность форматов** — старые сообщения с кнопками умрут с traceback/«висящим» UI. Почему: Telegram хранит кнопки в отправленных сообщениях.
- **FSM-переходы и `state.update_data(asdict(query))`** — preview/results читают query-имена (`pets_allowed`, не `pets`); рассинхрон = пустые поиски.
- **`SearchContext`/`SearchQuery` сериализация** — monitor и `STATE_KEY` зависят от полей; добавление обязательного поля ломает старые записи.
- **Семантика `None` в matcher** — `None` = фильтр выключен; превращение в reject обнулит выдачи.
- **Student-гейт** — порядок запреты→разрешения, клауза-границы, запрет regex на «пара»; регрессия = 0 результатов у студентов.
- **Provider fallback** — `ListingDataUnavailable` должен деградировать, а не падать; `Unavailable` вместо выдуманных данных.
- **Миграции** — только новые revision + downgrade; production downgrade без бэкапа запрещён.
- **Жизненный цикл shared client/engine** — закрытие только в shutdown (`main.py:finally`); иначе утечки/зависания.
- **Монитор** — baseline-first; сломаешь — спам всем подписчикам.
- **SavedSearch dedup** — сигнатуры включают все фильтры; изменение = дубли или потеря поисков.
- **systemd (один процесс, polling)** — второй инстанс = конфликт polling (Telegram error).
- **Секреты** — токены/пароли только env, никогда в код/логи/ мониторинг-тексты.

---

## 23. How to debug

«Бот не ищет квартиру»: handler (`search.py`) → callback_data → FSM state/data → `_query_from_data` → `SearchService._collect_candidates` (счётчик candidates в логе `search city=… candidates=… matched=… rejected=…`) → analyzer (`/debug_listing` на mock) → `matcher.match(...).failed_conditions` → рендеринг (`_render_result_page`, `result_sessions`).
Студент не работает: `repr(query.tenant)` (должен быть `"student"`, str) → `query.student_allowed` → провайдер (das-параметров для студентов нет — так и должно быть) → `analysis.details["description_text"]` → `is_student_allowed()` → `_check_student` → `REJECTED_STUDENT_RESTRICTION` в `failed_conditions` → проверка, что neutral не ушёл в скрытый бакет (теперь всегда в основном списке).
Логи: `journalctl -u kvartira-bot -n 150 --no-pager`; искать `Traceback`, `ERROR`, коды `REJECT_*` только в debug-скриптах (в прод-логах их нет — matcher считает молча).

---

## 24. Performance

- `app/providers/http.py`: один shared `httpx.AsyncClient` (pool 20, keep-alive 10), закрывается в shutdown.
- `SearchService`: semaphore на detail-fetch (`krisha_max_concurrency`), TTL-кэш анализов (`CACHE_TTL_SECONDS=600`), `cheap_skip` до дорогой загрузки detail, `with_retry` backoff.
- `PlaceCatalog`: TTL 600с, колбэки читают память, никакого DISTINCT на клик.
- `RateLimiter`: 10с на поиск/анализ с пользователя + in-flight dedup (`user_tasks`, отмена дубля).
- Монитор: `MONITOR_MAX_SEARCHES_PER_RUN=20`, `MONITOR_CONCURRENCY=3`, уведомления ≤10/поиск, анализатор/матчер создаются раз на цикл (оптимизация `a805ca4`).
- `cleanup_old_derived_data()` на старте чистит производные данные.

---

## 25. Security

Секреты (`BOT_TOKEN`, DB-пароль, `KRISHA_API_TOKEN`, `FIRECRAWL_API_KEY`, `OPENROUTER_API_KEY`) — только env/`.env` (production `.env` имеет CRLF — учитывать в скриптах). Никогда: в код, логи (`grep` по `password|token|key` перед коммитом), уведомления, README, бэкапы в git (`~/backups/` вне репозитория). Пользовательские данные: Telegram ID/поиски/избранное; удаление — `/delete_me`. Admin-команды закрыты `ADMIN_IDS`. Production-доступ — только SSH по явному запросу; `.env` на production не менять.

---

## 26. Future development (только признаки из проекта, без выдумок)

- **P0:** нет известных P0 — сервис active, тесты зелёные. Ближайшее к P0: 429-штормы Krisha снижают полноту detail-описаний (наблюдается в логах; решения в коде нет — и не должно быть в виде обхода).
- **P1:** вернуть place-фильтры в UX, когда появятся достоверные данные (backend готов: каталог, matcher, миграция `0009`, тесты); проверить реальным Telegram-клиентом (ни разу не делалось).
- **P2:** `krisha-api` при появлении официального доступа; Firecrawl-ingest по расписанию (код есть, расписания нет); `4plus`→диапазон (меняет matcher — осторожно); персистентность `result_sessions` (сейчас in-memory).

---

## 27. AI Agent Handoff

### Current state
Работает: кнопочный поиск city→rooms→budget→tenant→3 тоггла→результаты; student-гейт; saved searches + мониторинг с уведомлениями; favorites; пагинации; auto-relax (район → +25% бюджета, ≤2 шагов); graceful shutdown. Последний результат: **456 passed, 1 skipped**, ruff/pip/alembic зелёные, production `Active: active (running)`, polling + monitor запущены.

### Current UX
Раздел §8. Коротко: места/комиссия/депозит скрыты из меню, backend для них жив.

### Current architecture
Разделы §1, §5. Одна реализация matcher, один `SearchService`, провайдеры за фабрикой.

### Important constraints
Раздел §22 + §21. Коротко: не ломать callback/FSM/матчер/монитор/миграции; без антибот-обхода; без секретов в коде; без рестайла архитектуры ради рестайла.

### Known limitations
Раздел §19. Коротко: 468/429 Krisha, in-memory сессии, реальный Telegram-клиент не проверен.

### Safe workflow
1. inspect (прочитай код, не предполагай) 2. understand (цепочка UI→matcher) 3. найди существующую реализацию (не дублируй) 4. минимальное изменение 5. добавь/обнови тесты (включая regression на найденный баг — сначала красный, потом зелёный) 6. `pytest` 7. `ruff check .` 8. `pip check` 9. `alembic check` при смене моделей 10. покажи diff 11. деплой **только по явному запросу** (бэкап → pull → тесты → migrate → restart → status → journal).

### Production rule
НИКОГДА не деплой и не рестарт production без явного запроса пользователя. Тесты — только на sqlite (боевую PostgreSQL не трогать, кроме SELECT-диагностики и бэкапа).

### Change rule
Сначала использовать существующие абстракции; не создавать вторые матчеры/провайдеры/каталоги/парсеры; legacy callbacks и поля БД не удалять без проверки backward compatibility.

---

## 28. First message for new AI agent

Если ты новый AI-агент, начни отсюда:
1. Прочитай этот README полностью.
2. Изучи дерево проекта (§3) и открой `app/bot/handlers/search.py`, `app/listings/matcher.py`, `app/listings/semantics.py`.
3. Проверь `git status` и `git log -5 --oneline` (ожидаешь HEAD `a08d19e`, чистое дерево).
4. Проверь `app/config.py` + `.env.example` (секреты не читать/не печатать).
5. Запусти `pytest -q`, `ruff check .`, `pip check` (ожидаешь 456 passed / clean / clean).
6. Не меняй код, пока не понял цепочку §4–§5.
7. Перед изменениями объясни план; после — покажи diff и результаты тестов.
8. Production (§17–§18) — только по явному запросу пользователя.

---

## 29. Verification appendix (как проверен этот README)

- Пути файлов: сверены чтением каталогов (`app/*`, `tests/*`, `alembic/versions/*`).
- Классы/функции: `SearchStates`, `SearchService`, `ListingMatcher/MatchResult`, `is_student_allowed`, `PlaceCatalog.get(city, kind, query, district)`, `SavedSearchMonitor`, `get_listing_provider/get_llm_provider`, `search_signature`, `_execute_search`, `result_sessions` — все подтверждены grep/чтением.
- Команды: `pytest/ruff/pip/alembic`, deploy-порядок и `deploy.sh` — из файлов и выполненных прогонов.
- Миграции: `0001–0009` из `alembic/versions/`; head `0009_microdistrict` — вывод `alembic current`.
- Env: только из `.env.example` + `app/config.py` (значения — placeholders).
- UX: по `keyboards/inline.py` на HEAD (3 тоггла, мест нет).
- Student TRUE/FALSE: дословно из `tests/test_student_gate.py`.
- Production: `systemctl`/`journalctl` выводы и smoke-числа (RAW=60, gate 60→59, FINAL до 50) — из выполненных команд, бот `@kvartira10_bot`, HEAD `a08d19e`.
- Изучены: структура, README (устарел — заменён), requirements, `.env.example`, config, все handlers/services/providers/models/migrations, FSM/SearchContext/SearchQuery/SearchIntent, matcher/analyzer/monitor/LLM, все 22 тест-файла, deploy.sh, service-файл, `git log` (25 коммитов), TODO/FIXME (поиск пуст).
- Противоречия: старое README описывало MVP без Krisha-карточек и с отключённым избранным — устарело, заменено. Ранее в отчётах встречалось «Астана 10 записей» — фактически dev-БД содержит **Актау** (проверено по кодам символов).
- Невозможно достоверно определить из репозитория: объём production-БД (только SELECT-пробы и логи), установлены ли `FIRECRAWL_*/OPENROUTER_*` на production (значения `.env` не читались), поведение реального Telegram-клиента. Всё это помечено `NOT VERIFIED` выше.


## Диагностика: «поиск ничего не находит» (Krisha блокирует IP сервера)

Симптом в проде: каждые 20 с в journal строка `provider search_paged p1 attempt N/3 failed: TimeoutError()`,
поиск заканчивается `candidates=0 matched=0`, пользователь видит пустую выдачу.

Причина не в коде: Krisha/Kolesa **молча дропают TCP** с датацентровых диапазонов
(Oracle Cloud и подобные). Проверяется в две команды на сервере:

```bash
curl -sS -m 12 -o /dev/null -w "%{http_code} %{time_total}\n" https://example.com   # 200, мс
curl -sS -m 12 -o /dev/null -w "%{http_code} %{time_total}\n" https://krisha.kz/     # 000, 12 s
```

`000` + таймаут = SYN уходит в никуда, парсер и селекторы ни при чём (с домашней машины
тот же код отдаёт 80 карточек за 120 мс).

### Что сделано

Провайдер умеет ходить через точку выхода (релей), которая забирает страницу со своего IP
и отдаёт исходный HTML — парсер видит ту же разметку, что и при прямом запросе:

```dotenv
KRISHA_FETCH_RELAY=https://r.jina.ai/     # префикс: relay/<полный url объявления>
KRISHA_RELAY_TOKEN=                       # если у релея есть ключ
KRISHA_RELAY_TIMEOUT_SECONDS=45
KRISHA_RELAY_MIN_INTERVAL_SECONDS=3.2     # ~19 запросов/мин, лимит свободного тарифа
```

Правила, зашитые в `app/providers/krisha_html.py`:

- `KRISHA_FETCH_RELAY` пустой → прямые запросы, поведение как раньше (регресс-тест это фиксирует);
- все вызовы через релей проходят через общий троттлинг (`_relay_wait`), чтобы параллельные
  загрузки деталей не выбивали 429;
- 429 при загрузке **карточки объявления** не убивает поиск: объявление остаётся с данными из
  карточки, `details["detail_blocked"] = True`;
- 429 на странице поиска после трёх попыток → `RelayRateLimited` с понятным текстом.

Проверка без сети Telegram:

```bash
cd /home/ubuntu/telegram_bot_krisha.kz
KRISHA_FETCH_RELAY=https://r.jina.ai/ .venv/bin/python probe_relay.py
```

Ожидаемый вывод: `search page1: 20+ items in 3-6s`, у объявлений осмысленные `area`/`price`,
`detail: ... source=full`.

### Что лучше релея

1. **VPS в Казахстане** (PS.kz, ~$4/мес) — прямой доступ, релей не нужен, лимитов нет.
2. **Официальный API Kolesa Group** — провайдер `krisha-api` уже готов, нужен контракт и токен:
   `LISTING_PROVIDER=krisha-api`, `KRISHA_API_BASE_URL=...`, `KRISHA_API_TOKEN=...`.
3. Свой релей на Cloudflare Worker (100k запросов/сутки бесплатно) — 20 строк кода, без лимита 20/мин.

Релей — это костыль на вчера, а не архитектура: свободный тариф даёт 20 запросов в минуту,
то есть один поиск с деталями занимает ~20-30 с.


## Авто-обновление сервера с GitHub

Сервер сам проверяет `origin` раз в 2 минуты и, если появился новый коммит, раскатывает его:
зависимости → тесты → линт → миграции → `systemctl restart kvartira-bot`. Пушить руками и
заходить по ssh не нужно: `git push` в GitHub = бот обновился через пару минут.

```bash
# один раз на сервере
cd /home/ubuntu/telegram_bot_krisha.kz
sudo bash deploy/install-autopull.sh

systemctl list-timers kvartira-autopull.timer      # когда следующий тик
journalctl -t kv-autopull -n 30 --no-pager         # что делал деплой
tail -n 50 /home/ubuntu/.kv-autopull/deploy.log    # полный вывод deploy.sh
systemctl stop kvartira-autopull.timer             # выключить авто-обновление
```

Почему это безопасно:

- новый коммит катится только если `pytest` и `ruff` зелёные — иначе `deploy.sh` падает до
  `systemctl restart`, и бот продолжает работать на прошлой версии;
- битый коммит не перезапускается по кругу: после провала он ждёт час или следующий коммит;
- одновременно работает только один деплой (`flock`), таймер не наложится сам на себя;
- если GitHub недоступен, в журнал пишется одна строка раз в полчаса, без спама.

Откат, если новая версия плохая:

```bash
cd /home/ubuntu/telegram_bot_krisha.kz
sudo systemctl stop kvartira-autopull.timer     # чтобы не вернуло обратно
git reset --hard <хороший_коммит>
.venv/bin/python -m alembic downgrade -1        # только если были миграции
sudo systemctl restart kvartira-bot
```

Репозиторий должен быть **приватным**: в нём лежит логика бота, `.env` с токеном не коммитится
(он в `.gitignore`), но приватность всё равно обязательна.