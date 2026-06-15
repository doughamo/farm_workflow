"""
Shared CN1/ADAPT SDK bootstrap and decoding helpers (pythonnet bridge).

Requires the conda env setup described in CLAUDE.md (pythonnet installed,
sdk/cn1/Resources/*.xml copied to the conda env's base directory).
"""

import sys
from pathlib import Path

import clr

SDK_DIR = Path(__file__).resolve().parent.parent / "sdk" / "cn1"

_DLLS = [
    "Voyager2Plugin.dll",
    "CNHVoyager2.dll",
    "AgGateway.ADAPT.ApplicationDataModel.dll",
    "AgGateway.ADAPT.PluginManager.dll",
    "AgGateway.ADAPT.Representation.dll",
]

_plugin = None


def get_plugin():
    """Return the cached Voyager2Plugin instance, loading SDK DLLs on first call."""
    global _plugin
    if _plugin is not None:
        return _plugin

    sys.path.append(str(SDK_DIR))
    for dll in _DLLS:
        clr.AddReference(str(SDK_DIR / dll))

    from AgGateway.ADAPT.PluginManager import PluginFactory

    factory = PluginFactory(str(SDK_DIR))
    _plugin = factory.GetPlugin("Voyager2Plugin")
    return _plugin


def import_adm(path: str):
    """Import a CN1 data folder and return the first ApplicationDataModel."""
    plugin = get_plugin()
    adm_list = list(plugin.Import(str(path), None))
    return adm_list[0]


def decode_meter_value(sr, wd):
    """Decode a spatial record's meter value for a given WorkingData.

    Returns a (value, unit_or_code) tuple:
      - NumericValue:       (float value, unit-of-measure code string, e.g. 'kg')
      - EnumerationMember:  (string label, integer code)
      - anything else:      (None, None)
    """
    rep_value = sr.GetMeterValue(wd)
    inner = rep_value.Value
    type_name = inner.GetType().Name

    if type_name == "NumericValue":
        uom = inner.UnitOfMeasure
        return inner.Value, (uom.Code if uom else None)
    elif type_name == "EnumerationMember":
        return inner.Value, inner.Code

    return None, None


def resolve_machine_id(cat, op):
    """Resolve a machine/device identifier for an OperationData's equipment.

    Walks: OperationData.EquipmentConfigurationIds
           -> EquipmentConfiguration.Connector1Id/Connector2Id
           -> Connector.DeviceElementConfigurationId
           -> DeviceElementConfiguration.DeviceElementId
           -> DeviceElement.Description

    Returns the first resolvable DeviceElement description, or None if the
    chain cannot be resolved (e.g. equipment metadata absent for this op).
    """
    equipment_configs = {ec.Id.ReferenceId: ec for ec in cat.EquipmentConfigurations}
    connectors = {c.Id.ReferenceId: c for c in cat.Connectors}
    device_element_configs = {
        dec.Id.ReferenceId: dec for dec in cat.DeviceElementConfigurations
    }
    device_elements = {de.Id.ReferenceId: de for de in cat.DeviceElements}

    for ec_id in op.EquipmentConfigurationIds:
        ec = equipment_configs.get(ec_id)
        if ec is None:
            continue
        for connector_id in (ec.Connector1Id, ec.Connector2Id):
            connector = connectors.get(connector_id)
            if connector is None:
                continue
            dec = device_element_configs.get(connector.DeviceElementConfigurationId)
            if dec is None:
                continue
            de = device_elements.get(dec.DeviceElementId)
            if de is not None and getattr(de, "Description", None):
                return de.Description

    return None
