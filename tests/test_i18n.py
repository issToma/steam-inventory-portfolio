import ast
import string
from pathlib import Path

import pytest

from cs2market import i18n
from cs2market.i18n import RU, RU_PLURAL, tr, tr_n
from cs2market.ui.layout import NAV, NAV_BOTTOM, THEMES

SOURCE = Path(i18n.__file__).parent


def literal_calls() -> tuple[set[str], set[tuple[str, str]]]:
    """Every string literal passed to tr() and every (one, many) pair passed to tr_n()."""
    singles: set[str] = set()
    plurals: set[tuple[str, str]] = set()
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            args = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
            # tr_in() and tr_n_in() take the language first.
            if node.func.id in ("tr_in", "tr_n_in"):
                args = args[1:]
            if node.func.id in ("tr", "tr_in") and args and isinstance(args[0], str):
                singles.add(args[0])
            elif node.func.id in ("tr_n", "tr_n_in") and len(args) >= 3:
                one, many = args[1], args[2]
                if isinstance(one, str) and isinstance(many, str):
                    plurals.add((one, many))
    return singles, plurals


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_ui_string_has_a_russian_translation() -> None:
    singles, plurals = literal_calls()
    dynamic = {item.label for item in [*NAV, *NAV_BOTTOM]} | set(THEMES.values()) | {"All"}

    assert sorted((singles | dynamic) - RU.keys()) == []
    assert sorted(plurals - RU_PLURAL.keys()) == []


def test_translations_keep_placeholders() -> None:
    for english, russian in RU.items():
        assert placeholders(russian) == placeholders(english), english
    for (one, many), forms in RU_PLURAL.items():
        for form in forms:
            assert placeholders(form) == placeholders(one) == placeholders(many), one


@pytest.fixture
def russian(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "current_language", lambda: "ru")


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 минуту"),
        (2, "2 минуты"),
        (5, "5 минут"),
        (11, "11 минут"),
        (21, "21 минуту"),
        (22, "22 минуты"),
        (112, "112 минут"),
    ],
)
@pytest.mark.usefixtures("russian")
def test_russian_plurals(count: int, expected: str) -> None:
    assert tr_n(count, "{n} minute", "{n} minutes") == expected


@pytest.mark.usefixtures("russian")
def test_translation_fills_placeholders() -> None:
    assert tr("Added {name}", name="Kilowatt Case") == "Добавлено: Kilowatt Case"
    assert tr("Something not translated") == "Something not translated"


def test_english_is_the_default() -> None:
    assert tr("Portfolio") == "Portfolio"
    assert tr_n(1, "{n} item", "{n} items") == "1 item"
    assert tr_n(3, "{n} item", "{n} items") == "3 items"
