"""
OEM1 CompoundSplitter — splits compound signals into per-component messages.

TIRE_PRESSURE: 1 → 4 messages (FL, FR, RL, RR wheel positions)
ACCELERATION:  1 → 2 messages (longitudinal, lateral components)
"""


class CompoundSplitter:
    def split(self, message: dict) -> list[dict]:
        signal_type = message.get("signal_type", "")
        if signal_type == "TIRE_PRESSURE":
            return self._split_tire_pressure(message)
        if signal_type == "ACCELERATION":
            return self._split_acceleration(message)
        return [message]

    def _split_tire_pressure(self, message: dict) -> list[dict]:
        wheels = message.get("wheels", {})
        base = {k: v for k, v in message.items() if k != "wheels"}
        result = []
        for position in ("FL", "FR", "RL", "RR"):
            wheel_data = wheels.get(position, {})
            msg = dict(base)
            msg["wheel_position"] = position
            msg["value"] = wheel_data.get("value")
            msg["unit"] = wheel_data.get("unit")
            result.append(msg)
        return result

    def _split_acceleration(self, message: dict) -> list[dict]:
        components = message.get("components", {})
        base = {k: v for k, v in message.items() if k != "components"}
        result = []
        for component in ("longitudinal", "lateral"):
            comp_data = components.get(component, {})
            msg = dict(base)
            msg["component"] = component
            msg["value"] = comp_data.get("value")
            msg["unit"] = comp_data.get("unit")
            result.append(msg)
        return result
