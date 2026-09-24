"""Enigma2 entry point; core modules remain usable without Enigma2."""

from Plugins.Plugin import PluginDescriptor

from .ui.i18n import _

NAME = "TKGS Navigator"
DESCRIPTION = _("Local TKGS scan and channel ordering")


def open_plugin(session, **kwargs):
    from .ui.screen import NavigatorScreen

    session.open(NavigatorScreen)


updater = None


def session_start(reason, session=None, **kwargs):
    """Start the background updater; it stays idle unless the daily update is enabled."""
    global updater
    if reason == 0 and session is not None and updater is None:
        from .ui.auto import AutoUpdater

        updater = AutoUpdater(session)
        updater.start()


def Plugins(**kwargs):
    return [
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=session_start),
        PluginDescriptor(
            name=NAME,
            description=DESCRIPTION,
            where=PluginDescriptor.WHERE_PLUGINMENU,
            fnc=open_plugin,
        ),
        PluginDescriptor(
            name=NAME,
            description=DESCRIPTION,
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            fnc=open_plugin,
        ),
    ]
