"""Display-only Qt translations. Persistent data and logs retain source text."""
from pathlib import Path
import weakref

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator


LANGUAGES = {"zh_CN": "简体中文", "en": "English"}
LANGUAGE_KEY = "interface/language"
_language = "zh_CN"
_bindings = {}


class DisplayText(str):
    """A translated display value with its source, never inferred from user text."""
    def __new__(cls, source, values=(), parts=None):
        obj = super().__new__(cls, cls.render_parts(source, values, parts))
        obj.source, obj.values, obj.parts = source, values, parts
        return obj

    @staticmethod
    def render_parts(source, values, parts):
        if parts is not None:
            return "".join(render(part) for part in parts)
        text = QCoreApplication.translate("NodeBridge", source)
        return text.format(*(render(v) if isinstance(v, DisplayText) else v for v in values)) if values else text

    def translated(self):
        return self.render_parts(self.source, self.values, self.parts)

    def __add__(self, other):
        return DisplayText("", parts=(self, other))

    def __radd__(self, other):
        return DisplayText("", parts=(other, self))


def render(value):
    return value.translated() if isinstance(value, DisplayText) else str(value)


def _bind(target, setter, getter, value, prefix=()):
    key = (setter, prefix)
    identity = id(target)
    if identity not in _bindings:
        _bindings[identity] = (weakref.ref(target, lambda _ref: _bindings.pop(identity, None)), {})
    bindings = _bindings[identity][1]
    if isinstance(value, DisplayText):
        bindings[key] = (getter, value, str(value))
    else:
        bindings.pop(key, None)


def ui(function, *args, **kwargs):
    """Call a Qt display setter/constructor and bind only explicitly translated values."""
    result = function(*args, **kwargs)
    owner = getattr(function, "__self__", None)
    name = getattr(function, "__name__", "")
    properties = {"setText": "text", "setWindowTitle": "windowTitle", "setTitle": "title",
                  "setToolTip": "toolTip", "setStatusTip": "statusTip", "setPlaceholderText": "placeholderText",
                  "setHeaderLabel": None, "setInformativeText": "informativeText"}
    if owner is not None and name in properties and args:
        if name == "setHeaderLabel":
            _bind(owner.headerItem(), "setText", "text", args[0], (0,))
        else:
            _bind(owner, name, properties[name], args[-1], tuple(args[:-1]))
    elif name in {"addAction", "addMenu", "addButton"} and args and isinstance(args[0], DisplayText):
        if hasattr(result, "setTitle"):
            _bind(result, "setTitle", "title", args[0])
        elif hasattr(result, "setText"):
            _bind(result, "setText", "text", args[0])
    elif name in {"setHeaderLabels", "setHorizontalHeaderLabels"}:
        for i, value in enumerate(args[0]):
            if name == "setHeaderLabels":
                _bind(owner.headerItem(), "setText", "text", value, (i,))
            else:
                _bind(owner.horizontalHeaderItem(i), "setText", "text", value)
    elif name in {"addItem", "addItems"} and hasattr(owner, "setItemText"):
        values = args[0] if name == "addItems" else [args[0]]
        start = owner.count() - len(values)
        for i, value in enumerate(values, start):
            _bind(owner, "setItemText", "itemText", value, (i,))
    elif name in {"addTab", "setTabText"}:
        index = result if name == "addTab" else args[0]
        # Workspace tabs have fixed positions; terminal tabs get new bindings when titles update.
        _bind(owner, "setTabText", "tabText", args[-1], (index,))
    elif owner is None and args:
        if name == "QTreeWidgetItem":
            values = next((arg for arg in args if isinstance(arg, list)), [])
            for i, value in enumerate(values):
                _bind(result, "setText", "text", value, (i,))
            return result
        prop = "title" if name in {"QGroupBox", "QMenu"} else "text"
        if hasattr(result, prop) and hasattr(result, "set" + prop.title()):
            _bind(result, "set" + prop.title(), prop, args[0])
    return result


def retranslate_displays():
    from shiboken6 import isValid
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtCore import QObject
    for reference, bindings in list(_bindings.values()):
        target = reference()
        if target is None or not isValid(target):
            continue
        signal_owner = target if isinstance(target, QObject) else (
            target.treeWidget() if hasattr(target, "treeWidget") else
            target.tableWidget() if hasattr(target, "tableWidget") else None)
        blocker = QSignalBlocker(signal_owner) if signal_owner is not None else None
        for (setter, prefix), (getter, value, previous) in list(bindings.items()):
            # A user edit or a newer unbound value takes precedence over the old binding.
            if getattr(target, getter)(*prefix) != previous:
                bindings.pop((setter, prefix), None)
                continue
            text = value.translated()
            getattr(target, setter)(*prefix, text)
            bindings[(setter, prefix)] = (getter, value, text)
        del blocker


def selected_language(settings, system_locale=None):
    saved = settings.value(LANGUAGE_KEY, "", type=str)
    if saved in LANGUAGES:
        return saved
    name = system_locale if system_locale is not None else QLocale.system().name()
    return "zh_CN" if name.lower().startswith("zh") else "en"


def install_language(app, settings):
    """Install translators and update bound properties without rebuilding widgets."""
    global _language
    for translator in getattr(app, "_nodebridge_translators", []):
        app.removeTranslator(translator)
    language = selected_language(settings)
    translators = []
    if language == "en":
        translator = QTranslator(app)
        path = Path(__file__).with_name("assets") / "i18n" / "nodebridge_en.qm"
        if not translator.load(str(path)):
            raise RuntimeError(f"Cannot load interface translations: {path}")
        app.installTranslator(translator)
        translators.append(translator)
    else:
        translator = QTranslator(app)
        if translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
            translators.append(translator)
    app._nodebridge_translators = translators
    _language = language
    retranslate_displays()
    return language


def tr(source, *values):
    """Translate application-owned display text; format data only afterwards."""
    if isinstance(source, DisplayText) and not values:
        return source
    return DisplayText(source, values)


def add_language_menu(parent, view_menu, settings):
    from PySide6.QtGui import QActionGroup
    menu = ui(view_menu.addMenu, tr("语言"))
    group = QActionGroup(menu)
    group.setExclusive(True)
    selected = selected_language(settings)
    for code, label in LANGUAGES.items():
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(code == selected)
        action.setData(code)
        group.addAction(action)

    def choose(action):
        settings.setValue(LANGUAGE_KEY, action.data())
        settings.sync()
        install_language(QCoreApplication.instance(), settings)
        refresh = getattr(parent, "refresh_language", None)
        if refresh is not None:
            refresh()

    group.triggered.connect(choose)
    menu.language_actions = group
    return menu
