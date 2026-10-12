"""Каталог команд Saby (СБИС), доступных серверу.

Файл намеренно хранит ТОЛЬКО команды чтения. Это не фильтр, который можно
случайно обойти, а граница возможностей сервера: команды записи и удаления
сюда не попадают, поэтому исполнить их нечем — не через каталог, не через
любой другой инструмент. Единственный инструмент, который ходит на провод,
(`saby_call`) умеет адресовать исключительно запись из этого файла.

Что проверено на живой сессии, а что взято из справки
------------------------------------------------------
`params_root` и `result_key` помечены `verified: true`, только если подтверждены
настоящим вызовом. Это оказалось важным: команде у Saby ОБЯЗАТЕЛЬНО нужен её
корневой объект в params, и при params={} сервер отвечает «метод не найден»
(-32601) — хотя команда существует и называется верно.

Аналогично `result_key`: у СБИС.СписокИзмененийПриглашений выдача лежит под
ключом «СписокПриглашений», а не «Приглашение», как следовало из названия
раздела. По названию догадываться нельзя.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HOST = "online.sbis.ru"
SERVICE_PATH = "/service/?srv=1"
AUTH_PATH = "/auth/service/"

DOCS = "https://saby.ru/help/integration/api"


@dataclass(frozen=True)
class Command:
    """Одна команда чтения.

    command: точное имя команды для тела JSON-RPC, как в справке.
    params_root: корневой ключ, который обязан присутствовать в params.
        Пустая строка — команде достаточно пустых params. Клиент подставляет
        пустой объект, если агент ключ не передал: без него Saby отвечает
        «метод не найден», и это выглядит как поломка сервера, хотя она в
        данных.
    params_shape: подсказка агенту, какие ключи класть в params.
    result_key: ключ верхнего уровня в result, где лежат строки выдачи.
        Пустая строка означает, что result сам является массивом.
    pagination: сервер отдаёт постранично; см. pagination.py.
    page_size_max: потолок страницы, который сервер объявил сам.
    verified: True, если params_root и result_key подтверждены живым вызовом.
    """

    id: str
    command: str
    section: str
    summary: str
    endpoint: str = "service"
    needs_session: bool = True
    params_root: str = ""
    keywords: tuple[str, ...] = ()
    params_shape: dict[str, str] = field(default_factory=dict)
    required_params: tuple[str, ...] = ()
    result_key: str = ""
    paginated: bool = False
    verified: bool = False
    doc: str = ""

    @property
    def path(self) -> str:
        return AUTH_PATH if self.endpoint == "auth" else SERVICE_PATH

    # needs_session — отдельное поле, а не вывод из раздела или адреса.
    #
    # Живыми вызовами подтверждены три несовпадения, которые стоили отладки:
    #   • СБИС.ИнформацияОТекущемПользователе — по смыслу аутентификация,
    #     но живёт на /service/?srv=1 и сессию требует;
    #   • СБИС.СписокАккаунтов — живёт на /auth/service/, но сессию тоже
    #     требует («не передан идентификатор текущего пользователя»);
    #   • СБИС.Аутентифицировать — единственная, кто сессию не требует:
    #     она её и выдаёт.

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "section": self.section,
            "summary": self.summary,
            "verified": self.verified,
        }


# --- Документы и этапы ------------------------------------------------------

DOCUMENTS: tuple[Command, ...] = (
    Command(
        id="spisok_dokumentov",
        command="СБИС.СписокДокументов",
        section="documents",
        summary="Список документов указанного типа за период.",
        params_root="Фильтр",
        keywords=("список", "документы", "выборка", "реестр", "накладная",
                  "счет", "счёт", "акт", "договор", "list", "documents"),
        params_shape={
            "Фильтр": "объект отбора: Тип (обязателен, значение — идентификатор "
                      "типа из справочника Saby, не название!), ДатаС, ДатаПо, "
                      "Направление (Входящий/Исходящий), Состояние, Маска, "
                      "Регламент: {Название}, Контрагент, НашаОрганизация",
        },
        required_params=("Фильтр.Тип",),
        result_key="Документ",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/list_doc",
    ),
    Command(
        id="spisok_dokumentov_po_sobytiyam",
        command="СБИС.СписокДокументовПоСобытиям",
        section="documents",
        summary="Реестры «Входящие» и «Отправленные»: события за период.",
        params_root="Фильтр",
        keywords=("события", "реестр", "входящие", "исходящие", "отправленные",
                  "движение", "events", "incoming", "outgoing"),
        params_shape={
            "Фильтр": "объект отбора: ТипРеестра (Входящие, Отправленные, "
                      "Ответы контрагента), ДатаС, ДатаПо, ТипВложения, "
                      "Направление, Состояние, Маска",
        },
        required_params=("Фильтр.ТипРеестра",),
        result_key="Реестр",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/events_doc",
    ),
    Command(
        id="prochitat_dokument",
        command="СБИС.ПрочитатьДокумент",
        section="documents",
        summary="Последняя редакция документа по идентификатору.",
        params_root="Документ",
        keywords=("прочитать", "открыть", "документ", "редакция", "содержимое",
                  "тело", "read", "document"),
        params_shape={
            "Документ": "объект: Идентификатор (обязателен), Редакция, ДопПоля",
        },
        required_params=("Документ.Идентификатор",),
        # Проверено живьём: СБИС.ПрочитатьДокумент возвращает сам документ
        # верхним уровнем result — Идентификатор, Название, Номер, Контрагент,
        # Вложение, Событие. Обёртки «Документ» нет, и с неверным result_key
        # извлечение молча давало пустоту.
        result_key="",
        verified=True,
        doc=f"{DOCS}/all_methods/read_doc",
    ),
    Command(
        id="spisok_zadach",
        command="СБИС.СписокЗадач",
        section="documents",
        summary="Задачи из разделов «На мне» и «От меня».",
        params_root="Фильтр",
        keywords=("задачи", "задача", "на мне", "от меня", "работа", "tasks"),
        params_shape={
            "Фильтр": "объект отбора: ТипСписка (НаМне / ОтМеня), Тип, "
                      "ИдентификаторДокумента, Регламент, "
                      "Сотрудник: {Идентификатор} или {Фамилия, Имя, Отчество}",
        },
        required_params=("Фильтр.ТипСписка",),
        result_key="Документ",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/task_list",
    ),
    Command(
        id="spisok_izmeneniy",
        command="СБИС.СписокИзменений",
        section="documents",
        summary="Какие изменения по документам и с какого момента их брать.",
        params_root="Фильтр",
        keywords=("изменения", "изменение", "события", "история", "дельта",
                  "sync", "changes", "updates"),
        params_shape={
            "Фильтр": "объект: ДатаВремяС (обязателен, «ДД.ММ.ГГГГ ЧЧ.ММ.СС»), "
                      "ДатаВремяПо, Тип, Регламент, НашаОрганизация",
        },
        required_params=("Фильтр.ДатаВремяС",),
        result_key="Документ",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/changeslist",
    ),
    Command(
        id="spisok_sluzhebnyh_etapov",
        command="СБИС.СписокСлужебныхЭтапов",
        section="documents",
        summary="Необработанные служебные этапы.",
        params_root="Фильтр",
        keywords=("служебные", "этапы", "этап", "извещения", "получение",
                  "stages", "notifications"),
        params_shape={"Фильтр": "объект: НашаОрганизация, ИдентификаторДокумента"},
        result_key="Документ",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/stage_doc",
    ),
    Command(
        id="informaciya_o_sluzhebnyh_etapah",
        command="СБИС.ИнформацияОСлужебныхЭтапах",
        section="documents",
        summary="Организации с необработанными служебными документами и их счётчик.",
        params_root="Фильтр",
        keywords=("информация", "служебные", "этапы", "организации", "счетчик",
                  "необработанные", "info", "stages"),
        params_shape={"Фильтр": "объект: ТолькоНаличиеСобытий (Да/Нет), НашаОрганизация"},
        result_key="НашаОрганизация",
        verified=True,
        doc=f"{DOCS}/all_methods/info_doc",
    ),
)

# --- Аутентификация ---------------------------------------------------------
# Раздел, где есть команды, идущие без заголовка сессии: ими входят и получают
# сессию. Адрес у них другой — /auth/service/, а не /service/.

AUTH: tuple[Command, ...] = (
    Command(
        id="autentificirovat",
        command="СБИС.Аутентифицировать",
        section="auth",
        summary="Вход по логину и паролю, возвращает идентификатор сессии.",
        endpoint="auth",
        needs_session=False,  # единственная команда без сессии: она её выдаёт
        params_root="Параметр",
        keywords=("вход", "войти", "зайти", "логин", "пароль", "авторизация",
                  "аутентификация", "сессия", "login", "auth", "signin"),
        params_shape={
            "Параметр": "объект: Логин (обязателен), Пароль (обязателен), "
                        "НомерАккаунта (если кабинетов несколько)",
        },
        required_params=("Параметр.Логин", "Параметр.Пароль"),
        verified=True,
        doc=f"{DOCS}/all_methods/auth_one",
    ),
    Command(
        id="informaciya_o_polzovatele",
        command="СБИС.ИнформацияОТекущемПользователе",
        section="auth",
        summary="Кто вошёл: ФИО и ссылки на внешние сервисы аккаунта.",
        params_root="Параметр",
        keywords=("пользователь", "кто", "я", "профиль", "фио", "кто я",
                  "user", "me", "profile"),
        params_shape={"Параметр": "пустой объект; ДопПоля='СписокВнешнихПровайдеров' "
                                  "чтобы добавить внешние сервисы"},
        result_key="Пользователь",
        verified=True,
        doc=f"{DOCS}/all_methods/auth_infouser",
    ),
    Command(
        id="spisok_akkauntov",
        command="СБИС.СписокАккаунтов",
        section="auth",
        summary="Все аккаунты, доступные текущему пользователю.",
        # Проверено живьём: адрес /auth/service/ И заголовок сессии оба нужны.
        endpoint="auth",
        needs_session=True,
        params_root="",  # и при этом с пустыми params
        keywords=("аккаунты", "кабинеты", "кабинет", "аккаунт", "accounts"),
        result_key="Список",
        verified=True,
        doc=f"{DOCS}/all_methods/list_of_acc",
    ),
)

# --- Электронная подпись ----------------------------------------------------

SIGNATURE: tuple[Command, ...] = (
    Command(
        id="spisok_sertifikatov",
        command="СБИС.СписокСертификатов",
        section="signature",
        summary="Действительные сертификаты: собственные и делегированные.",
        params_root="Фильтр",
        keywords=("сертификаты", "сертификат", "перечень", "список",
                  "электронная", "подпись", "ЭП", "certificates"),
        result_key="Сертификат",
        verified=True,
        doc=f"{DOCS}/signature",
    ),
    Command(
        id="proverit_sertifikaty",
        command="СБИС.ПроверитьСертификаты",
        section="signature",
        summary="Статус и срок действия сертификатов.",
        # Корневой объект на боевой сессии не подтвердился: «Сертификат» даёт
        # отказ по данным (-32602), «Параметр» — тоже. params_root пустой, чтобы
        # сервер ничего не подставлял от себя: структуру передаст агент, и если
        # она верная, команда отработает.
        params_root="",
        verified=False,
        keywords=("проверить", "статус", "срок", "действие", "истекают",
                  "сертификаты", "check", "status"),
        params_shape={
            "Сертификат": "структура НЕ УСТАНОВЛЕНА на живом контуре — "
                          "передайте по справке Saby",
        },
        doc=f"{DOCS}/signature",
    ),
)

# --- Доверенности и МЧД -----------------------------------------------------

POA: tuple[Command, ...] = (
    Command(
        id="mchd_status",
        command="sabyMchd.GetStatus",
        section="poa",
        summary="Статус и реквизиты МЧД из реестра ФНС по её номеру.",
        params_root="MchdNumber",
        keywords=("мчд", "доверенность", "статус", "реестр", "фнс", "trust"),
        params_shape={"MchdNumber": "строка, регистрационный номер МЧД (обязателен)"},
        required_params=("MchdNumber",),
        result_key="Mchd",
        verified=True,
        doc=f"{DOCS}/all_methods/powers/sabymchd_getstatus",
    ),
    Command(
        id="mchd_content",
        command="sabyMchd.GetContent",
        section="poa",
        summary="Содержимое МЧД из реестра ФНС по её номеру.",
        params_root="",
        verified=False,
        keywords=("мчд", "доверенность", "содержимое", "скачать", "реестр",
                  "фнс", "content"),
        params_shape={
            "MchdNumber": "структура НЕ УСТАНОВЛЕНА на живом контуре: ключ "
                          "не подошёл ни в одной из четырёх форм",
        },
        doc=f"{DOCS}/all_methods/powers/sabymchd_getcontent",
    ),
)

# --- Контрагенты ------------------------------------------------------------

COUNTERAGENTS: tuple[Command, ...] = (
    Command(
        id="informaciya_o_kontragente",
        command="СБИС.ИнформацияОКонтрагенте",
        section="counteragents",
        summary="Карточка контрагента по ИНН и КПП, включая идентификатор ЭДО.",
        params_root="СвЮЛ",
        keywords=("контрагент", "контрагенты", "карточка", "инн", "кпп",
                  "идентификатор", "эдо", "проверить", "counterparty", "partner"),
        params_shape={
            "СвЮЛ": "для организации: ИНН (обязателен), КПП, КодФилиала",
            "СвФЛ": "для ИП и физлица: ИНН (обязателен), КодФилиала",
        },
        required_params=("ИНН",),
        verified=True,
        doc=f"{DOCS}/all_methods/companyinfo",
    ),
    Command(
        id="prochitat_priglasenie",
        command="СБИС.ПрочитатьПриглашение",
        section="counteragents",
        summary="Информация о событии приглашения к обмену.",
        params_root="",
        verified=False,
        keywords=("приглашение", "прочитать", "событие", "invitation"),
        params_shape={
            "Приглашение": "структура не подтверждена на живом контуре — "
                           "передайте по справке Saby",
        },
        doc=f"{DOCS}/all_methods/read_invitation",
    ),
    Command(
        id="spisok_izmeneniy_priglaseniy",
        command="СБИС.СписокИзмененийПриглашений",
        section="counteragents",
        summary="События по приглашениям к обмену.",
        params_root="Фильтр",
        keywords=("приглашения", "изменения", "события", "список",
                  "invitation", "changes"),
        params_shape={"Фильтр": "объект: ИдентификаторПриглашения, ДатаВремяС"},
        # Проверено живьём: выдача лежит под «СписокПриглашений», а не под
        # «Приглашение», как следовало бы из названия раздела.
        result_key="СписокПриглашений",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/list_invitation_changes",
    ),
)

# --- Наши организации и подразделения ---------------------------------------

ORGANIZATIONS: tuple[Command, ...] = (
    Command(
        id="spisok_nashih_organizaciy",
        command="СБИС.СписокНашихОрганизаций",
        section="organizations",
        summary="Наши организации и их реквизиты: ИНН, КПП, GLN.",
        params_root="Фильтр",
        keywords=("организации", "наши", "организация", "компания", "инн",
                  "кпп", "глн", "companies"),
        params_shape={"Фильтр": "Необязательный объект отбора"},
        result_key="НашаОрганизация",
        paginated=True,
        verified=True,
        doc=f"{DOCS}/all_methods/company",
    ),
    Command(
        id="spisok_podrazdeleniy",
        command="СБИС.СписокПодразделений",
        section="organizations",
        summary="Подразделения личного кабинета.",
        params_root="Параметр",
        keywords=("подразделения", "подразделение", "отдел", "департамент",
                  "структура", "departments"),
        result_key="Подразделение",
        verified=True,
        doc=f"{DOCS}/all_methods/list_squad",
    ),
    Command(
        id="saby_department_read",
        command="sabyDepartment.Read",
        section="organizations",
        summary="Подразделения по списку идентификаторов (до 500 за раз).",
        params_root="departments",
        keywords=("подразделение", "подразделения", "идентификаторы", "читать",
                  "department"),
        params_shape={"departments": "массив UUID, максимум 500"},
        required_params=("departments",),
        # Проверено живьём: result сам является массивом, обёртки нет.
        result_key="",
        verified=True,
        doc=f"{DOCS}/all_methods/read_division",
    ),
    Command(
        id="saby_ourcompany_list",
        command="sabyOurCompany.List",
        section="organizations",
        summary="Данные организации по внешнему id либо связке ИНН+КПП+филиал.",
        params_root="filter",
        keywords=("организация", "компания", "наша", "инн", "кпп", "company"),
        params_shape={"filter": "объект: id, inn, kpp, branchCode, countryCode"},
        required_params=("filter",),
        result_key="",
        verified=True,
        doc="https://saby.ru/help/partner/api/sabycompany_list",
    ),
)

# --- Сервисные --------------------------------------------------------------

SERVICE: tuple[Command, ...] = (
    Command(
        id="informaciya_o_versii",
        command="СБИС.ИнформацияОВерсии",
        section="service",
        summary="Версия внешнего интерфейса и серверное время Saby.",
        params_root="Параметр",
        keywords=("версия", "версию", "интерфейс", "время", "сервер", "version"),
        result_key="ВнешнийИнтерфейс",
        verified=True,
        doc=f"{DOCS}/all_methods/infover",
    ),
)


ALL_COMMANDS: tuple[Command, ...] = (
    DOCUMENTS + AUTH + SIGNATURE + POA + COUNTERAGENTS + ORGANIZATIONS + SERVICE
)

BY_ID: dict[str, Command] = {c.id: c for c in ALL_COMMANDS}
BY_COMMAND: dict[str, Command] = {c.command: c for c in ALL_COMMANDS}

SECTIONS: dict[str, tuple[Command, ...]] = {
    "documents": DOCUMENTS,
    "auth": AUTH,
    "signature": SIGNATURE,
    "poa": POA,
    "counteragents": COUNTERAGENTS,
    "organizations": ORGANIZATIONS,
    "service": SERVICE,
}

SECTION_TITLES: dict[str, str] = {
    "documents": "Документы и этапы",
    "auth": "Аутентификация",
    "signature": "Электронная подпись",
    "poa": "Доверенности и МЧД",
    "counteragents": "Контрагенты",
    "organizations": "Наши организации и подразделения",
    "service": "Сервисные",
}


def get(command_id: str) -> Command | None:
    """Точный поиск по id, затем по полному имени команды.

    Второй путь нужен, чтобы агент мог передать «СБИС.СписокДокументов»
    из карточки напрямую, не переводя имя в latin.
    """
    if command_id in BY_ID:
        return BY_ID[command_id]
    return BY_COMMAND.get(command_id)


def with_root(cmd: Command, params: dict[str, Any] | None) -> dict[str, Any]:
    """Подставить корневой объект команды, если его нет.

    Проверено живьём: у команды, которой нужен «Фильтр», пустые params дают
    -32601 «метод не найден». Выглядит как поломка сервера, хотя не найден
    ровно тот метод, чьё params не разобрались. Подстановка пустого объекта
    безопасна: сервер сам скажет, чего не хватает, и это будет понятнее.
    """
    out = dict(params or {})
    if cmd.params_root and cmd.params_root not in out:
        out[cmd.params_root] = {}
    return out


def health() -> dict[str, Any]:
    """Сводка по каталогу. Используется в doctor и в тестах."""
    return {
        "commands": len(ALL_COMMANDS),
        "sections": {name: len(cmds) for name, cmds in SECTIONS.items()},
        "unverified": sorted(c.id for c in ALL_COMMANDS if not c.verified),
    }
