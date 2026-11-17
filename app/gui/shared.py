"""Общее для модулей окна: константы, необязательные модули, стиль и виджеты.

Вынесено из window.py, чтобы страницы/действия/игры/обновления не импортировали само окно, иначе
получился бы циклический импорт."""
from typing import Any

from PySide6.QtWidgets import QWidget

from app import diag, lists, logs, stats, strategies, tester
from app.paths import ROOT
from app.runner import Runner

from . import style
from .style import COLORS, app_icon, init_fonts
from .widgets import InputRow, Pages, PowerButton, Rail, SettingRow, StatusChip, Switch, q
from .worker import Bridge, Worker

try:  # модули приложения есть всегда, кроме урезанной сборки: тогда окно работает без обновлений/игр
    from app import updater as _updater
except Exception:  # noqa: BLE001
    _updater = None
try:
    from app import gamewatch as _gamewatch
except Exception:  # noqa: BLE001
    _gamewatch = None
# None проверяется по месту (в урезанной сборке его и нет); в тестах эти имена подменяются целиком
updater: Any = _updater
gamewatch: Any = _gamewatch

TASK = "ZapretAuto"
AUTO = "Авто (результат подбора)"
UPDATE_EVERY = 6 * 3600  # s between background update checks

__all__ = ["AUTO", "COLORS", "UPDATE_EVERY", "Any", "Bridge", "InputRow", "Pages", "PowerButton", "QWidget", "ROOT",
           "Rail", "Runner", "SettingRow", "StatusChip", "Switch", "TASK", "Worker", "app_icon", "diag", "gamewatch",
           "init_fonts", "lists", "logs", "q", "stats", "strategies", "style", "tester", "updater"]
