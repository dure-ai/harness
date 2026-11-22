"""YAML reading shared by the scenario and config loaders."""

from collections.abc import Hashable
from typing import Any, cast, override

import yaml
from yaml.constructor import ConstructorError


def _construct_unique_mapping(
    loader: yaml.constructor.SafeConstructor, node: yaml.MappingNode, deep: bool = False
) -> dict[Hashable, object]:
    """Build a mapping, raising if a key appears twice.

    Args:
        loader: The loader building the document.
        node: The YAML mapping node.
        deep: Whether to construct nested objects immediately.

    Returns:
        The mapping.

    Raises:
        yaml.constructor.ConstructorError: If a key repeats; the message names the key and line.
    """
    seen: set[object] = set()
    pairs = cast("list[tuple[yaml.Node, yaml.Node]]", node.value)
    for key_node, _ in pairs:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue
        try:
            key = loader.construct_object(key_node, deep=True)
            if key in seen:
                line = key_node.start_mark.line + 1
                raise ConstructorError(None, None, f"duplicate key {key!r} (line {line})")
            seen.add(key)
        except TypeError:  # unhashable key: the base class reports it
            break
    return yaml.constructor.SafeConstructor.construct_mapping(loader, node, deep)


class PurePythonUniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects a repeated key in any mapping (plain `safe_load` keeps the last).

    The pure-Python parser: `load_yaml` uses it only when PyYAML was built without libyaml.
    """

    @override
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        """See `_construct_unique_mapping`."""
        return _construct_unique_mapping(self, node, deep)


if getattr(yaml, "__with_libyaml__", False):

    class UniqueKeyLoader(yaml.CSafeLoader):  # pyright: ignore[reportRedeclaration]
        """`PurePythonUniqueKeyLoader` on libyaml's parser, several times faster.

        Only scanning and parsing move to C: the constructor and resolver are the same Python
        classes. The two parsers load every YAML file in this repository to the same values
        (tests compare them all), but they are not equivalent on every input: libyaml accepts a
        tab inside a plain scalar (`a: b<TAB>c`) that the pure parser rejects, rejects an escaped
        surrogate pair that the pure parser decodes, and its error messages omit the snippet of
        the offending line.
        """

        @override
        def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
            """See `_construct_unique_mapping`."""
            return _construct_unique_mapping(self, node, deep)

else:  # pragma: no cover - PyYAML wheels ship libyaml

    class UniqueKeyLoader(PurePythonUniqueKeyLoader):
        """`PurePythonUniqueKeyLoader`: this PyYAML has no libyaml."""


def load_yaml(text: str) -> Any:
    """Parse YAML safely, rejecting a repeated mapping key (plain `safe_load` keeps the last).

    Args:
        text: The YAML document.

    Returns:
        The parsed document.

    Raises:
        yaml.YAMLError: If the YAML is invalid or a key repeats; the message names the key and line.
    """
    return yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506  # a SafeLoader or CSafeLoader subclass


def dump_yaml(data: Any) -> str:
    r"""`yaml.safe_dump(data, sort_keys=False, allow_unicode=True)`, by libyaml when it writes the same text.

    libyaml's emitter is several times faster. It writes the same text as the pure-Python one
    except that it escapes characters outside the Basic Multilingual Plane (emoji become
    `\U0001F600` in double quotes); a document whose libyaml text holds `\U` is written again by
    the Python emitter. Tests compare both emitters on every scenario file in the repository.

    Args:
        data: Plain data (dicts, lists, strings, numbers).

    Returns:
        The YAML document.
    """
    if getattr(yaml, "__with_libyaml__", False):
        text: str = yaml.dump(data, Dumper=yaml.CSafeDumper, sort_keys=False, allow_unicode=True)
        if "\\U" not in text:
            return text
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
