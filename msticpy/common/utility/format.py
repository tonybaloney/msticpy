# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""Formatting and checking functions."""
import builtins
import re
import uuid
from typing import Any

from ..._version import VERSION
from .types import export

__version__ = VERSION
__author__ = "Ian Hellen"


@export
def is_valid_uuid(uuid_str: str) -> bool:
    """
    Return true if `uuid_str` is a value GUID/UUID.

    Parameters
    ----------
    uuid_str : str
        String to test

    Returns
    -------
    bool
        True if valid GUID/UUID.

    """
    if not uuid_str:
        return False
    try:
        uuid.UUID(uuid_str)
    except (ValueError, TypeError):
        return False
    return True


@export
def valid_pyname(identifier: str) -> str:
    """
    Return legal Python identifier, which doesn't collide with builtins.

    Parameters
    ----------
    identifier : str
        The input identifier

    Returns
    -------
    str
        The cleaned identifier

    """
    builtin_names = set(dir(builtins))
    if identifier in builtin_names:
        identifier = f"{identifier}_bi"
    identifier = re.sub("[^a-zA-Z0-9_]", "_", identifier)
    if identifier[0].isdigit():
        identifier = f"n_{identifier}"
    return identifier


@export
def string_empty(string: str) -> bool:
    """Return True if the input string is None or whitespace."""
    return not (bool(string) or isinstance(string, str) and bool(string.strip()))


@export
def is_not_empty(test_object: Any) -> bool:
    """Return True if the test_object is not None or empty."""
    if test_object:
        return bool(test_object.strip()) if isinstance(test_object, str) else True
    return False


# String escapes
@export
def escape_windows_path(str_path: str) -> str:
    """Escape backslash characters in a string."""
    return str_path.replace("\\", "\\\\") if is_not_empty(str_path) else str_path


@export
def unescape_windows_path(str_path: str) -> str:
    """Remove escaping from backslash characters in a string."""
    return str_path.replace("\\\\", "\\") if is_not_empty(str_path) else str_path
