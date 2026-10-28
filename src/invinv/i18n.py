"""Interface translations. English is the source language and the default.

UI code wraps every visible English string in tr(); a missing translation falls back to
English. Plural phrases go through tr_n(), since Russian has three plural forms.
"""

from typing import Literal

from nicegui import app

type Language = Literal["en", "ru"]

LANGUAGES: dict[Language, str] = {"en": "English", "ru": "Русский"}
DEFAULT_LANGUAGE: Language = "en"


def current_language() -> Language:
    try:
        language = app.storage.user.get("language", DEFAULT_LANGUAGE)
    except RuntimeError:  # outside a page request, e.g. in tests
        return DEFAULT_LANGUAGE
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def set_language(language: Language) -> None:
    app.storage.user["language"] = language


def tr(text: str, /, **values: object) -> str:
    """Translate an English UI string and fill in its {placeholders}."""
    return tr_in(current_language(), text, **values)


def tr_in(language: Language, text: str, /, **values: object) -> str:
    """tr() for a given language, for text built outside a page (say, a Telegram message)."""
    translated = RU.get(text, text) if language == "ru" else text
    return translated.format(**values) if values else translated


def tr_n(count: int, one: str, many: str, /, **values: object) -> str:
    """Translate a phrase that depends on a count, given as "{n} item" / "{n} items"."""
    return tr_n_in(current_language(), count, one, many, **values)


def tr_n_in(language: Language, count: int, one: str, many: str, /, **values: object) -> str:
    if language == "ru" and (one, many) in RU_PLURAL:
        forms = RU_PLURAL[(one, many)]
        last_two, last = count % 100, count % 10
        if last == 1 and last_two != 11:
            text = forms[0]
        elif 2 <= last <= 4 and not 12 <= last_two <= 14:
            text = forms[1]
        else:
            text = forms[2]
    else:
        text = one if count == 1 else many
    return text.format(n=count, **values)


RU_PLURAL: dict[tuple[str, str], tuple[str, str, str]] = {
    ("{n} day", "{n} days"): ("{n} день", "{n} дня", "{n} дней"),
    ("{n} hour", "{n} hours"): ("{n} час", "{n} часа", "{n} часов"),
    ("{n} minute", "{n} minutes"): ("{n} минуту", "{n} минуты", "{n} минут"),
    ("{n} item", "{n} items"): ("{n} предмет", "{n} предмета", "{n} предметов"),
    ("{n} sale", "{n} sales"): ("{n} продажа", "{n} продажи", "{n} продаж"),
    ("Updating · {n} task queued", "Updating · {n} tasks queued"): (
        "Обновляем · в очереди {n} задача",
        "Обновляем · в очереди {n} задачи",
        "Обновляем · в очереди {n} задач",
    ),
    ("{n} match among items seen before.", "{n} matches among items seen before."): (
        "{n} совпадение среди уже встречавшихся предметов.",
        "{n} совпадения среди уже встречавшихся предметов.",
        "{n} совпадений среди уже встречавшихся предметов.",
    ),
}

RU: dict[str, str] = {
    # Navigation and frame
    "Portfolio": "Портфель",
    "Watchlist": "Watchlist",
    "Alerts": "Алерты",
    "Market": "Рынок",
    "Settings": "Настройки",
    "soon": "скоро",
    "Theme": "Тема",
    "Language": "Язык",
    "Dark": "Тёмная",
    "Dark dimmed": "Тёмно-серая",
    "Light": "Светлая",
    "Search items…": "Поиск предметов…",
    "Collector is off": "Сборщик выключен",
    "Steam limited requests · waiting {minutes} min": (
        "Steam ограничил запросы · ждём {minutes} мин"
    ),
    "Prices are up to date": "Цены актуальны",
    "Connect Steam": "Подключить Steam",
    # Settings
    "Appearance": "Внешний вид",
    "Games": "Игры",
    "The games of your inventory appear here after the first import.": "Игры вашего инвентаря появятся здесь после первого импорта.",
    "Choose which games' inventories are imported. Changes apply at the next sync.": "Выберите, инвентари каких игр импортировать. Изменения применятся при следующей синхронизации.",
    "Saved; the inventory is synced again now.": "Сохранено; инвентарь сейчас синхронизируется заново.",
    "Data collection": "Сбор данных",
    "Collector": "Сборщик",
    "running": "работает",
    "turned off": "выключен",
    "Tasks waiting now": "Задач в очереди сейчас",
    "Pause between requests to Steam": "Пауза между запросами к Steam",
    "{seconds} s": "{seconds} с",
    "Important items refresh every": "Важные предметы обновляются каждые",
    "Cheaper items refresh every": "Дешёвые предметы обновляются каждые",
    "6 to 24 hours": "6–24 часа",
    "Inventory import every": "Импорт инвентаря каждые",
    "These values come from the INVINV_* environment variables; see .env.example. The pause cannot be set below 8 seconds.": (
        "Эти значения задаются переменными окружения INVINV_* (см. .env.example). "
        "Паузу нельзя сделать меньше 8 секунд."
    ),
    # Shared
    "All": "Всё",
    "Price": "Цена",
    "Item": "Предмет",
    "Cancel": "Отмена",
    "Save": "Сохранить",
    "Delete": "Удалить",
    "Add": "Добавить",
    "Added": "Добавлено",
    "Status": "Статус",
    "just now": "только что",
    "{span} ago": "{span} назад",
    "in {span}": "через {span}",
    "any moment now": "с минуты на минуту",
    "less than a minute": "меньше минуты",
    # Search
    "Find an item (name, part of it, or a market URL)": (
        "Найти предмет (название, его часть или ссылка на рынок)"
    ),
    "redline, breakout case, karambit doppler…": "redline, breakout case, karambit doppler…",
    "Search Steam": "Искать в Steam",
    "Any type": "Любой тип",
    "Rifle": "Винтовка",
    "Sniper rifle": "Снайперская винтовка",
    "Pistol": "Пистолет",
    "SMG": "Пистолет-пулемёт",
    "Shotgun": "Дробовик",
    "Machine gun": "Пулемёт",
    "Equipment (Zeus)": "Снаряжение (Zeus)",
    "Knife": "Нож",
    "Gloves": "Перчатки",
    "Sticker": "Наклейка",
    "Case": "Кейс",
    "Agent": "Агент",
    "Graffiti": "Граффити",
    "Patch": "Нашивка",
    "Charm": "Брелок",
    "Music kit": "Набор музыки",
    "Key": "Ключ",
    "Collectible (pins)": "Коллекционное (значки)",
    "Classic": "Обычные",
    "Any wear": "Любой износ",
    "{price} · {listings} listed": "{price} · {listings} лотов",
    "Not what you need? Press Search Steam.": "Нет нужного? Нажмите «Искать в Steam».",
    "Nothing among items seen before. Press Search Steam to ask Steam.": (
        "Среди уже встречавшихся предметов ничего нет. Нажмите «Искать в Steam»."
    ),
    "Type at least two characters or pick a type.": (
        "Введите хотя бы два символа или выберите тип."
    ),
    "Asking Steam… (requests are spaced out, this can take up to a minute)": (
        "Спрашиваем Steam… (запросы идут с паузами, это может занять до минуты)"
    ),
    "Steam is rate limiting us; try again in about {minutes} min. "
    "Items seen before are still searchable.": (
        "Steam ограничил запросы; попробуйте примерно через {minutes} мин. "
        "Поиск по уже встречавшимся предметам работает."
    ),
    "Steam search failed: {error}": "Поиск в Steam не удался: {error}",
    "Steam found {count} (at most 10 per search; refine if needed).": (
        "Steam нашёл {count} (не больше 10 за поиск; уточните запрос, если нужно)."
    ),
    "Steam found nothing. Check the spelling or loosen the filters.": (
        "Steam ничего не нашёл. Проверьте написание или ослабьте фильтры."
    ),
    "That does not look like a market link to a single item.": (
        "Это не похоже на ссылку на конкретный предмет на рынке."
    ),
    # Portfolio
    "Value": "Стоимость",
    "Where": "Где",
    "Qty": "Кол-во",
    "Share": "Доля",
    "P&L": "Прибыль",
    "Inventory": "Инвентарь",
    "Manual": "Вручную",
    "Positions": "Позиции",
    "You receive after fees": "Вы получите после комиссии",
    "Profit / loss": "Прибыль / убыток",
    "after fees, positions with a purchase price": "после комиссии, по позициям с ценой покупки",
    "Waiting for prices": "Ждут цен",
    "Add items that are not in your inventory": "Добавить предметы, которых нет в инвентаре",
    "For example, storage unit contents: Steam does not show them in the inventory.": (
        "Например, содержимое хранилищ: Steam не показывает его в инвентаре."
    ),
    "Add to portfolio": "В портфель",
    "Add item": "Добавить предмет",
    "All games": "Все игры",
    "Game": "Игра",
    "Pinned positions stay when items leave the inventory, for example into a storage unit.": (
        "Закреплённые позиции остаются, когда предметы уходят из инвентаря, например в хранилище."
    ),
    "Pin all inventory items": "Закрепить все предметы инвентаря",
    "Unpin all": "Открепить все",
    "All inventory items pinned": "Все предметы инвентаря закреплены",
    "All items unpinned": "Все предметы откреплены",
    "Top gainers, {period}": "Лидеры роста, {period}",
    "Top losers, {period}": "Лидеры падения, {period}",
    "all time": "за всё время",
    "Portfolio value": "Стоимость портфеля",
    "Now": "Сейчас",
    "For the period · {period}": "За период · {period}",
    "For all time": "За всё время",
    "since {date}, when the last of your items came to the market": "с {date}, когда на рынке появился последний из ваших предметов",
    "from {value}": "с {value}",
    "Change": "Изменение",
    "Highest": "Максимум",
    "Lowest": "Минимум",
    "Your Steam inventory could not be imported. See Settings for details.": (
        "Не удалось импортировать инвентарь Steam. Подробности — в настройках."
    ),
    "Connect your Steam inventory to import your items automatically.": (
        "Подключите инвентарь Steam, чтобы предметы импортировались автоматически."
    ),
    "Open settings": "Открыть настройки",
    "Inventory synced {when} · next update {next}": "Инвентарь обновлён {when} · следующее обновление {next}",
    "Added {name}": "Добавлено: {name}",
    "Quantity comes from your Steam inventory.": "Количество берётся из инвентаря Steam.",
    "Quantity": "Количество",
    "Where it is kept": "Где хранится",
    "Storage unit #1": "Хранилище #1",
    "Purchase price per item, $ (optional)": "Цена покупки за штуку, $ (необязательно)",
    "Purchase date (optional, YYYY-MM-DD)": "Дата покупки (необязательно, ГГГГ-ММ-ДД)",
    "Check the price and the date.": "Проверьте цену и дату.",
    "Quantity must be at least 1.": "Количество должно быть не меньше 1.",
    # Account
    "Connect your Steam inventory": "Подключите инвентарь Steam",
    "Paste a link to your Steam profile. Your items are imported automatically and kept up "
    "to date; nothing else is needed from your account.": (
        "Вставьте ссылку на свой профиль Steam. Предметы импортируются и обновляются "
        "автоматически; больше от вашего аккаунта ничего не нужно."
    ),
    "Profile link or SteamID64": "Ссылка на профиль или SteamID64",
    "Connect": "Подключить",
    "That does not look like a Steam profile link.": "Это не похоже на ссылку на профиль Steam.",
    "Steam is busy right now; try again in a few minutes.": (
        "Steam сейчас занят; попробуйте через несколько минут."
    ),
    "Steam does not know this profile. Check the link.": (
        "Steam не знает такого профиля. Проверьте ссылку."
    ),
    "Could not reach Steam; try again in a minute.": (
        "Не удалось связаться со Steam; попробуйте через минуту."
    ),
    "Connected, but the profile is not public yet.": "Подключено, но профиль пока не открыт.",
    "Connected as {name}. Importing your items…": "Подключено как {name}. Импортируем предметы…",
    "Steam account": "Аккаунт Steam",
    "Sync now": "Обновить сейчас",
    "Change account": "Сменить аккаунт",
    "Disconnect": "Отключить",
    "⚠ Your inventory is hidden by Steam's privacy settings.": (
        "⚠ Инвентарь скрыт настройками приватности Steam."
    ),
    "⚠ The last import failed: {error}": "⚠ Последний импорт не удался: {error}",
    "Next attempt {when}.": "Следующая попытка {when}.",
    "⏳ Importing your inventory… this usually takes a minute.": (
        "⏳ Импортируем инвентарь… обычно это занимает минуту."
    ),
    "✓ Synced {when} · {items}": "✓ Обновлено {when} · {items}",
    "Next update {when}.": "Следующее обновление {when}.",
    "How to make your inventory visible": "Как открыть инвентарь",
    "1. Open your [Steam privacy settings]({url}) (log in to Steam in this browser if asked).\n"
    "2. Set **My profile** and **Inventory** to **Public**. "
    '"Friends only" is not enough: this app does not log in to Steam.\n'
    "3. Come back and press **Sync now**. Steam may take a few minutes to apply it.": (
        "1. Откройте [настройки приватности Steam]({url}) (если попросит, войдите в Steam "
        "в этом браузере).\n"
        "2. Поставьте **Мой профиль** и **Инвентарь** в значение **Открытый**. "
        "«Только для друзей» не подойдёт: приложение не входит в Steam.\n"
        "3. Вернитесь и нажмите **Обновить сейчас**. Steam может применить настройку "
        "не сразу."
    ),
    "Checking your inventory…": "Проверяем инвентарь…",
    "Disconnected. Its positions are kept and come back if you connect it again.": (
        "Отключено. Позиции аккаунта сохранены и вернутся, если подключить его снова."
    ),
    # Watchlist
    "Items you follow without owning them.": "Предметы, за которыми вы следите, не владея ими.",
    "In watchlist": "В watchlist",
    "Removed {name} from the watchlist": "{name} убран из watchlist",
    "Sales/day": "Продаж/день",
    "Added {name}; history arrives in about a minute": (
        "Добавлено: {name}; история появится примерно через минуту"
    ),
    "waiting for exchange rate": "ждём курс валюты",
    "error: {error}": "ошибка: {error}",
    "ok": "ок",
    "no sales on record": "продаж не было",
    "waiting for first fetch": "ждём первой загрузки",
    # Item page
    "Item not found.": "Предмет не найден.",
    "Waiting for Steam's {currency} → USD rate; the collector fetches it right after the "
    "first history download.": (
        "Ждём курс Steam {currency} → USD; сборщик получит его сразу после первой загрузки истории."
    ),
    "No price history yet. Either the collector has not fetched it, or the item has never "
    "been sold on the market.": (
        "Истории цен пока нет: либо сборщик её ещё не загрузил, либо предмет ни разу не "
        "продавался на рынке."
    ),
    "Volatility (30d, daily)": "Волатильность (30д, дневная)",
    "Sales per day (30d)": "Продаж в день (30д)",
    "You receive after fee": "Вы получите после комиссии",
    "Sales": "Продажи",
    "Steam returned prices in {currency}; converted to USD at Steam's own rate "
    "({rate} USD per unit).": (
        "Steam отдал цены в {currency}; пересчитано в USD по курсу самого Steam "
        "({rate} USD за единицу)."
    ),
    "Fee calculator": "Калькулятор комиссии",
    "Hide outliers": "Скрывать выбросы",
    "Hides single odd sales, such as one sticker craft sold far above the usual price. Metrics always ignore them.": (
        "Скрывает одиночные необычные продажи, например крафт с наклейками намного дороже "
        "обычной цены. Метрики их всегда не учитывают."
    ),
    "Listing price (buyer pays), $": "Цена лота (платит покупатель), $",
    "You receive {proceeds}, fees {fees}": "Вы получите {proceeds}, комиссия {fees}",
    "Remove from watchlist": "Убрать из watchlist",
    "You hold {quantity} · worth {value}": "У вас {quantity} шт. · на сумму {value}",
    "Prices of this item are not collected yet. Add it to the watchlist or the portfolio and its full history arrives within a minute.": (
        "Цены этого предмета пока не собираются. Добавьте его в watchlist или в портфель, "
        "и полная история появится в течение минуты."
    ),
    "Add to watchlist": "Добавить в watchlist",
    # Alerts
    "24 hours": "24 часа",
    "7 days": "7 дней",
    "Add alert": "Добавить алерт",
    "Added. This is already true now; you will be notified the next time it is.": (
        "Добавлено. Условие уже выполняется сейчас; уведомление придёт, когда оно выполнится "
        "в следующий раз."
    ),
    "Alert added": "Алерт добавлен",
    "Alerts always show up in InvInv. To also get them in Telegram:": (
        "Алерты всегда видны в InvInv. Чтобы получать их и в Telegram:"
    ),
    "In Telegram, message @BotFather, send /newbot and copy the token.": (
        "Напишите в Telegram @BotFather, отправьте /newbot и скопируйте токен."
    ),
    "Paste the token below and save it.": "Вставьте токен ниже и сохраните.",
    "Send any message to your new bot, then press Find my chat.": (
        "Отправьте любое сообщение своему новому боту и нажмите «Найти мой чат»."
    ),
    "Alerts for {name}": "Алерты: {name}",
    "Alerts show up here and under the bell.": "Алерты появляются здесь и под колокольчиком.",
    "Alerts will go to {chat}": "Алерты будут приходить в {chat}",
    "Bot token": "Токен бота",
    "Chat: {chat}": "Чат: {chat}",
    "Choose": "Выбрать",
    "Connected to @{bot}. Now send it any message.": (
        "Подключено к @{bot}. Теперь отправьте ему любое сообщение."
    ),
    "Enter a value above zero.": "Введите значение больше нуля.",
    "Falls by {pct}% within {window}": "Падение на {pct}% за {window}",
    "Find my chat": "Найти мой чат",
    "Get notified when a price reaches a level or moves fast.": (
        "Уведомления, когда цена достигает уровня или резко меняется."
    ),
    "InvInv is connected: alerts will arrive here.": "InvInv подключён: алерты будут приходить сюда.",
    "Message language": "Язык сообщений",
    "New alert": "Новый алерт",
    "No alerts for this item yet.": "Для этого предмета алертов пока нет.",
    "No alerts yet. Add one here or from an item's page.": (
        "Алертов пока нет. Добавьте здесь или на странице предмета."
    ),
    "No chat yet": "Чат ещё не найден",
    "No messages to the bot yet. Send it any message and try again.": (
        "Боту ещё никто не писал. Отправьте ему любое сообщение и попробуйте снова."
    ),
    "Nothing has fired yet.": "Пока ничего не срабатывало.",
    "Notifications": "Уведомления",
    "Notify when": "Сообщить, когда",
    "Paste the token from @BotFather first.": "Сначала вставьте токен от @BotFather.",
    "Price at or above {price}": "Цена {price} и выше",
    "Price at or below {price}": "Цена {price} и ниже",
    "Price falls by": "Цена упадёт на",
    "Price falls to": "Цена упадёт до",
    "Price rises by": "Цена вырастет на",
    "Price rises to": "Цена вырастет до",
    "Rises by {pct}% within {window}": "Рост на {pct}% за {window}",
    "Rules": "Правила",
    "Save token": "Сохранить токен",
    "Send a test message": "Отправить тестовое сообщение",
    "Sent; check Telegram.": "Отправлено, проверьте Telegram.",
    "Set up Telegram to get them on your phone.": "Подключите Telegram, чтобы получать их на телефон.",
    "Telegram alerts": "Алерты в Telegram",
    "Telegram did not accept the token: {error}": "Telegram не принял токен: {error}",
    "Telegram disconnected": "Telegram отключён",
    "fired {ago}": "сработал {ago}",
    "in 24 hours": "за 24 часа",
    "in 7 days": "за 7 дней",
    "now {price}": "сейчас {price}",
    "now {price}, {change} in {window}": "сейчас {price}, {change} за {window}",
    "off": "выключен",
    "waiting": "ждёт",
    # Price loading progress
    "Loading prices · {priced} of {total} · ~{eta} left · {pace} s/request": (
        "Загружаем цены · {priced} из {total} · осталось ~{eta} · {pace} с/запрос"
    ),
    "One request to Steam every {pace} s": "Один запрос к Steam раз в {pace} с",
    "Resume now": "Продолжить сейчас",
    "Steam limits requests per IP address. If you switched to another IP (say, another VPN "
    "server), continue right away instead of waiting.": (
        "Steam ограничивает запросы по IP-адресу. Если вы сменили IP (например, другой сервер "
        "VPN), можно продолжить сразу, не дожидаясь."
    ),
    "Resumed. If Steam still answers 429, the next pause is longer.": (
        "Продолжаем. Если Steam снова ответит 429, следующая пауза будет дольше."
    ),
    "{seconds} s now · adaptive, {fastest} to {slowest} s": (
        "сейчас {seconds} с · адаптивно, от {fastest} до {slowest} с"
    ),
    # Current price
    "Last sale": "Последняя продажа",
    "Median sale, 24h": "Медиана продаж, 24ч",
    "cheapest listing · {ago}": "самый дешёвый лот · {ago}",
    "median of recent sales": "медиана последних продаж",
    "nothing listed · median of recent sales": "лотов нет · медиана последних продаж",
}
