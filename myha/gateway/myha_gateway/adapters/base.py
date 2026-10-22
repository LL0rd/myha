"""Types shared by the device adapters and the policy.

This first part holds only the closed action set; task 3.2 adds the adapter protocol and its
dataclasses (``DeviceCapabilities``, ``StateView``, ``HACall``, ``Expectation``) to this module.
"""

from enum import StrEnum


class Action(StrEnum):
    """The closed set of product actions (design §4.1); any other string is unknown."""

    METADATA_READ = "metadata.read"
    STATE_READ = "state.read"
    HISTORY_READ = "history.read"
    SWITCH_ON = "switch.on"
    SWITCH_OFF = "switch.off"
    LIGHT_ON = "light.on"
    LIGHT_OFF = "light.off"
    LIGHT_SET_LEVEL = "light.set_level"
    CLIMATE_SET_TEMPERATURE = "climate.set_temperature"
    CLIMATE_SET_MODE = "climate.set_mode"
    COVER_OPEN = "cover.open"
    COVER_CLOSE = "cover.close"
    COVER_STOP = "cover.stop"
    COVER_SET_POSITION = "cover.set_position"


READ_ACTIONS: frozenset[Action] = frozenset(
    {Action.METADATA_READ, Action.STATE_READ, Action.HISTORY_READ}
)
WRITE_ACTIONS: frozenset[Action] = frozenset(Action) - READ_ACTIONS
