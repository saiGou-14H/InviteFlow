"""Product roles; internal workers and providers are not user roles.

Sessions and route-level role checks are implemented in inviteflow.auth.
Business Hooks remain responsible for object ownership and operation permissions.
"""

from enum import Enum


class Role(str, Enum):
    USER = "user"
    ADMIN = "admin"
