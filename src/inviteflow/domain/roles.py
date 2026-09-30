"""Product roles; internal workers and providers are not user roles.

This enumeration declares the supported role contract only. Session authentication
and authorization are still unimplemented in the application skeleton.
"""

from enum import Enum


class Role(str, Enum):
    USER = "user"
    ADMIN = "admin"
