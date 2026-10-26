"""YAML reading shared by the scenario and config loaders."""

from collections.abc import Hashable
from typing import Any, override

import yaml
from yaml.constructor import ConstructorError


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects a repeated key in any mapping (plain `safe_load` keeps the last)."""

    @override
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        """Build a mapping, raising if a key appears twice.

        Args:
            node: The YAML mapping node.
            deep: Whether to construct nested objects immediately.

        Returns:
            The mapping.

        Raises:
            yaml.constructor.ConstructorError: If a key repeats; the message names the key and line.
        """
        seen: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            try:
                key = self.construct_object(key_node, deep=True)
                if key in seen:
                    line = key_node.start_mark.line + 1
                    raise ConstructorError(None, None, f"duplicate key {key!r} (line {line})")
                seen.add(key)
            except TypeError:  # unhashable key: the base class reports it
                break
        return super().construct_mapping(node, deep)


def load_yaml(text: str) -> Any:
    """Parse YAML safely, rejecting a repeated mapping key (plain `safe_load` keeps the last).

    Args:
        text: The YAML document.

    Returns:
        The parsed document.

    Raises:
        yaml.YAMLError: If the YAML is invalid or a key repeats; the message names the key and line.
    """
    return yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506  # SafeLoader subclass
