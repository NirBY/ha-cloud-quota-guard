"""Public automation references for explaining the impact of integration cutoff."""


def related_automations(entities, devices, automations):
    """Match explicit entity/device references; never claim exhaustive dependencies."""
    result = {}
    for automation in automations:
        entity_refs = set(automation.referenced_entities)
        device_refs = set(automation.referenced_devices)
        for entry_id in entities.keys() | devices.keys():
            matches = entity_refs & entities.get(entry_id, set())
            device_matches = device_refs & devices.get(entry_id, set())
            if not matches and not device_matches:
                continue
            result.setdefault(entry_id, []).append(
                {
                    "entity_id": automation.entity_id,
                    "name": automation.name or automation.entity_id,
                    "automation_id": automation.unique_id,
                    "entities": sorted(matches),
                    "devices": sorted(device_matches),
                }
            )
    return result


def collect_relationships(hass):
    """Read registry metadata and HA's parsed automation reference properties."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entities, devices = {}, {}
    for entity in er.async_get(hass).entities.values():
        if entity.config_entry_id:
            entities.setdefault(entity.config_entry_id, set()).add(entity.entity_id)
    for device in dr.async_get(hass).devices.values():
        for entry_id in device.config_entries:
            devices.setdefault(entry_id, set()).add(device.id)
    component = hass.data.get("automation")
    if component is None or not hasattr(component, "entities"):
        return {"available": False, "entries": {}}
    return {
        "available": True,
        "entries": related_automations(entities, devices, component.entities),
    }
