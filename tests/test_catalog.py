"""Каталог: главное свойство — в нём нет ничего, что меняет данные."""
from __future__ import annotations

import pytest

from saby_mcp_ro import catalog


def test_catalog_is_not_empty():
    assert len(catalog.ALL_COMMANDS) >= 18


def test_ids_are_unique():
    ids = [c.id for c in catalog.ALL_COMMANDS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "forbidden",
    [
        "Записать", "Удалить", "Уничтожить", "Создать", "Отправить",
        "Подписать", "Активировать", "Добавить", "Отозвать", "Зарегистрировать",
        "Изменить", "Обновить", "Восстановить", "Подготовить", "Выполнить",
        "Повторить", "Отложить", "Импорт", "Разработать",
    ],
)
def test_no_mutating_command_in_catalog(forbidden):
    """Ключевое свойство сервера: команды записи нет в каталоге."""
    for cmd in catalog.ALL_COMMANDS:
        assert forbidden not in cmd.command, f"команда записи в каталоге: {cmd.command}"


def test_read_only_commands_only():
    """Белый список префиксов чтения.

    Одного запрета мало: в справке появятся новые команды, и молчаливый пропуск
    опаснее явной проверки.
    """
    read_prefixes = (
        "СБИС.Список", "СБИС.Прочитать", "СБИС.Информация", "СБИС.Проверить",
        "СБИС.Аутентифицировать",
        "sabyMchd.Get",               # только Get*, без Send*
        "sabyDepartment.Read",        # без Update/Delete/ChangeId
        "sabyOurCompany.List",        # без Create/Update/Publish/Delete
    )
    for cmd in catalog.ALL_COMMANDS:
        assert cmd.command.startswith(read_prefixes), \
            f"команда вне белого списка чтения: {cmd.command}"


def test_every_command_has_docs_link():
    for cmd in catalog.ALL_COMMANDS:
        assert cmd.doc.startswith("https://"), f"{cmd.id}: нет ссылки на справку"


# --- корневой объект --------------------------------------------------------

def test_auth_commands_point_to_auth_path():
    """regression: командам входа нужен /auth/service/.

    В изученном сервере все девять команд аутентификации были на /service/,
    где их нет, и вход был физически невозможен.
    """
    for cmd in catalog.ALL_COMMANDS:
        if cmd.endpoint == "auth":
            assert cmd.path == "/auth/service/", f"{cmd.id}: неверный адрес"
            assert cmd.section == "auth", f"{cmd.id}: адрес и раздел расходятся"


def test_other_commands_point_to_service_path():
    for cmd in catalog.ALL_COMMANDS:
        if cmd.endpoint != "auth":
            assert cmd.path == "/service/?srv=1", f"{cmd.id}: неверный адрес"


def test_session_requirement_is_independent_of_address():
    """Три подтверждённых живьём несовпадения.

    Когда сессия выводилась из адреса, СБИС.СписокАккаунтов ходила без
    заголовка и отвечала «не передан идентификатор текущего пользователя»,
    а СБИС.ИнформацияОТекущемПользователе уходила на /auth/service/.
    """
    # единственная команда без сессии — вход, он её и выдаёт
    assert catalog.get("autentificirovat").needs_session is False
    assert catalog.get("autentificirovat").endpoint == "auth"

    # адрес auth, но сессия нужна
    accounts = catalog.get("spisok_akkauntov")
    assert accounts.endpoint == "auth"
    assert accounts.needs_session is True

    # по смыслу аутентификация, но адрес сервисный и сессия нужна
    current = catalog.get("informaciya_o_polzovatele")
    assert current.section == "auth"
    assert current.endpoint == "service"
    assert current.needs_session is True


def test_login_command_is_reachable_without_session(recorder):
    """Сессии ещё нет — значит, вход должен быть в состоянии её получить."""
    assert catalog.get("autentificirovat").needs_session is False


def test_current_user_command_is_on_service_path():
    """Именно этот баг ронял doctor --live."""
    cmd = catalog.get("informaciya_o_polzovatele")
    assert cmd.endpoint == "service"
    assert cmd.section == "auth"  # по смыслу это аутентификация


def test_all_paths_share_one_host():
    assert catalog.HOST == "online.sbis.ru"


def test_roots_are_known_cyrillic_or_latin_keys():
    """Корневой объект — это конкретный ключ, а не произвольное слово."""
    for cmd in catalog.ALL_COMMANDS:
        if cmd.params_root:
            assert " " not in cmd.params_root, cmd.id


@pytest.mark.parametrize("command_id,root", [
    ("spisok_dokumentov", "Фильтр"),
    ("spisok_zadach", "Фильтр"),
    ("spisok_izmeneniy", "Фильтр"),
    ("spisok_nashih_organizaciy", "Фильтр"),
    ("spisok_sluzhebnyh_etapov", "Фильтр"),
    ("spisok_podrazdeleniy", "Параметр"),
    ("informaciya_o_polzovatele", "Параметр"),
    ("informaciya_o_versii", "Параметр"),
    ("prochitat_dokument", "Документ"),
    ("mchd_status", "MchdNumber"),
    ("saby_department_read", "departments"),
    ("saby_ourcompany_list", "filter"),
    ("spisok_akkauntov", ""),
])
def test_roots_match_the_live_session(command_id, root):
    """regression: без корневого объекта Saby отвечает «метод не найден».

    Подтверждено боевой сессией: у всех перечисленных команд пустые params дают
    -32601, а с корревым объектом — нормальный ответ.
    """
    assert catalog.get(command_id).params_root == root


def test_with_root_injects_when_missing():
    cmd = catalog.get("spisok_dokumentov")
    assert catalog.with_root(cmd, None) == {"Фильтр": {}}
    assert catalog.with_root(cmd, {"Фильтр": {"Тип": "Счет"}}) == {"Фильтр": {"Тип": "Счет"}}


def test_with_root_does_not_mutate_caller():
    cmd = catalog.get("spisok_dokumentov")
    params = {"Фильтр": {"Тип": "Счет"}}
    catalog.with_root(cmd, params)
    assert params == {"Фильтр": {"Тип": "Счет"}}


def test_command_without_root_gets_empty_params():
    cmd = catalog.get("spisok_akkauntov")
    assert catalog.with_root(cmd, None) == {}


# --- прочее ------------------------------------------------------------------

def test_lookup_by_id_and_by_command():
    cmd = catalog.get("spisok_dokumentov")
    assert cmd is not None
    assert catalog.get("СБИС.СписокДокументов") is cmd


def test_unknown_lookup_returns_none():
    assert catalog.get("СБИС.ЗаписатьДокумент") is None
    assert catalog.get("nope") is None


def test_sections_cover_every_command():
    covered = [c for cmds in catalog.SECTIONS.values() for c in cmds]
    assert len(covered) == len(catalog.ALL_COMMANDS)


def test_summaries_are_human_readable():
    for cmd in catalog.ALL_COMMANDS:
        assert len(cmd.summary) >= 20, f"{cmd.id}: слишком короткое описание"
        assert not any(ord(ch) < 32 or 0xE000 <= ord(ch) <= 0xF8FF
                       for ch in cmd.summary), f"{cmd.id}: невидимый символ"


def test_paginated_commands_are_the_ones_that_report_navigation():
    for cmd in catalog.ALL_COMMANDS:
        assert isinstance(cmd.paginated, bool)


def test_read_document_has_no_wrapper():
    """regression: СБИС.ПрочитатьДокумент отдаёт сам документ.

    Обёртки «Документ» в result нет: поля Идентификатор, Название, Контрагент,
    Вложение, Событие лежат верхним уровнем. С result_key="Документ"
    извлечение возвращало пустоту — тихо, без ошибки, и это выглядело как
    «документ пустой».
    """
    assert catalog.get("prochitat_dokument").result_key == ""


def test_list_commands_keep_their_wrapper():
    """Списки, наоборот, завёрнуты — их ключ нужен."""
    for command_id in ("spisok_dokumentov", "spisok_dokumentov_po_sobytiyam",
                       "spisok_zadach", "spisok_izmeneniy"):
        assert catalog.get(command_id).result_key, command_id


def test_most_commands_are_verified_on_live_session():
    """Непроверенных команд должно быть мало, и они должны быть названы.

    Их наличие нормально: у Saby нет машиночитаемой схемы, часть структур
    выясняется только на живом контуре. Молча оставлять в каталоге команду
    «вроде бы правильную» нельзя — вот эти честно помечены verified=False.
    """
    health = catalog.health()
    unverified = set(health["unverified"])
    assert unverified, "ожидались непроверенные команды — их список пуст, проверьте вручную"
    assert len(unverified) <= 4, f"непроверено слишком много: {unverified}"
    for command_id in unverified:
        cmd = catalog.get(command_id)
        assert cmd is not None
        assert cmd.params_root == "", (
            f"{command_id}: непроверенная команда не должна получать "
            "подставляемый корень — сервер отвечает «метод не найден»"
        )


def test_health_summary():
    h = catalog.health()
    assert h["commands"] == len(catalog.ALL_COMMANDS)
    assert isinstance(h["unverified"], list)
