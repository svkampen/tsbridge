"""
utils.plugins - Plugin loading functionality
"""

from pathlib import Path
from typing import List, Dict, Union

from types import ModuleType
import importlib
import re
import logging

logger = logging.getLogger("plugin-loader")
DEPENDS_RE = re.compile(r"\* depends: (.+)")

Plugin = str
Dependency = str

class PluginLoader:
    """
    Infobot's plugin loader.

    Generates a dependency graph and returns a list of loaded modules when
    load_all() is called.
    """
    def __init__(self, plugin_directory: str = "plugins", blacklist: List[Plugin] = None):
        """ Initializes the PluginLoader by generating a dependency graph """
        self.plugin_package = self.plugin_directory = plugin_directory
        self.graph: Dict[Path, List[Dependency]] = {}
        self.blacklist = set(blacklist or [])

        path = Path(self.plugin_directory)
        plugin_paths = []
        for subpath in path.iterdir():
            if (subpath.is_dir() and (subpath / '__init__.py').exists()) or \
               (subpath.name.endswith('.py')):
                if subpath.stem not in self.blacklist:
                    plugin_paths.append(subpath)

        for path in plugin_paths:
            self.parse_depends(path)

    def parse_depends(self, path: Path) -> None:
        if path.is_dir():
            main_file = str(path / '__init__.py')
        else:
            main_file = str(path)

        with open(main_file) as plugin:
            for line in plugin:
                if '* noload' in line:
                    logger.info(f"Not loading plugin {path.stem} "
                                 "due to noload directive")
                    return

                match = DEPENDS_RE.search(line)
                if match:
                    self.graph[path] = match.group(1).split(', ')
            if path not in self.graph and path.name != '__init__.py':
                # This plugin does not yet define a dependency line.
                # Therefore, we act like it has no dependencies.
                self.graph[path] = []

    def load_plugin(self, plugin_path: Union[Path, str], name: str = None) -> ModuleType:
        if not name:
            assert isinstance(plugin_path, Path)
            name = f"{self.plugin_package}." + plugin_path.stem

        plugin = importlib.import_module(name)
        return plugin

    def check_impossible_loads(self) -> None:
        for plugin, deps in self.graph.items():
            blacklisted_deps = set(deps) & self.blacklist
            if blacklisted_deps:
                verb_form = "are" if len(blacklisted_deps) > 1 else "is"
                raise DependencyError(f"Plugin {plugin.stem} requires {', '.join(deps)},"
                                      f" which {verb_form} blacklisted.")

    def load_all(self) -> List[ModuleType]:
        self.check_impossible_loads()

        plugins = []
        self.load_plugin('__init__.py', name='plugins')
        while (self.graph):
            satisfied_plugins = {plugin:deps for plugin,deps in self.graph.items()
                                 if not deps}

            for satisfied_plugin, _ in satisfied_plugins.items():
                plugins.append(self.load_plugin(satisfied_plugin))
                del self.graph[satisfied_plugin]
                for plugin, deps in self.graph.items():
                    # This try clause is here because .remove throws when the
                    # item is not in the list
                    try:
                        deps.remove(satisfied_plugin.stem)
                    except ValueError:
                        pass

        logger.info(f'Loaded {len(plugins)} plugins ({plugins})')
        return plugins

class DependencyError(Exception):
    pass
