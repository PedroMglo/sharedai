"""Errors raised by Resource Governor clients and services."""


class ResourceGovernorError(RuntimeError):
    """Base class for Resource Governor failures."""


class GovernorUnavailableError(ResourceGovernorError):
    """The authoritative governor could not be reached."""


class GovernorContractError(ResourceGovernorError):
    """A payload did not match the shared contract."""


class LeaseDeniedError(ResourceGovernorError):
    """A caller tried to use a denied lease as if it were granted."""
