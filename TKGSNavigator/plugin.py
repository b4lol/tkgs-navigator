"""Enigma2 entry point; core modules remain usable without Enigma2."""
from Plugins.Plugin import PluginDescriptor

NAME = "TKGS Navigator"
DESCRIPTION = "Local TKGS scan and channel ordering"


def open_plugin(session, **kwargs):
    from .ui.screen import NavigatorScreen
    session.open(NavigatorScreen)


def Plugins(**kwargs):
    return [PluginDescriptor(name=NAME, description=DESCRIPTION,
                             where=PluginDescriptor.WHERE_PLUGINMENU, fnc=open_plugin),
            PluginDescriptor(name=NAME, description=DESCRIPTION,
                             where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=open_plugin)]
