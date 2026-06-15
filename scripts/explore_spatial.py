import clr, sys
sys.path.append(r'C:\Farm_Data\farm_workflow\sdk\cn1')
clr.AddReference(r'C:\Farm_Data\farm_workflow\sdk\cn1\Voyager2Plugin.dll')
clr.AddReference(r'C:\Farm_Data\farm_workflow\sdk\cn1\CNHVoyager2.dll')
clr.AddReference(r'C:\Farm_Data\farm_workflow\sdk\cn1\AgGateway.ADAPT.ApplicationDataModel.dll')
clr.AddReference(r'C:\Farm_Data\farm_workflow\sdk\cn1\AgGateway.ADAPT.PluginManager.dll')
clr.AddReference(r'C:\Farm_Data\farm_workflow\sdk\cn1\AgGateway.ADAPT.Representation.dll')

from AgGateway.ADAPT.PluginManager import PluginFactory

factory = PluginFactory(r'C:\Farm_Data\farm_workflow\sdk\cn1')
plugin = factory.GetPlugin('Voyager2Plugin')

extract_path = r'C:\Farm_Data\farm_workflow\sdk\SampleFiles\extracted'
adm_list = list(plugin.Import(extract_path, None))
adm = adm_list[0]

ld = list(adm.Documents.LoggedData)[0]
op = list(ld.OperationData)[0]

device_element_uses = list(op.GetDeviceElementUses(0))
deu = device_element_uses[0]
working_datas = list(deu.GetWorkingDatas())

# Get values from first spatial record passing WorkingData object directly
sr = list(op.GetSpatialRecords())[0]
print(f'Timestamp: {sr.Timestamp}')
print(f'Lat: {sr.Geometry.Y}')
print(f'Lon: {sr.Geometry.X}')
print('Values:')
for wd in working_datas:
    try:
        rep_value = sr.GetMeterValue(wd)
        # rep_value is a NumericRepresentationValue / EnumeratedRepresentationValue
        # whose .Value is the actual NumericValue / EnumerationMember
        inner = rep_value.Value
        code = wd.Representation.Code
        if inner.GetType().Name == 'NumericValue':
            uom = inner.UnitOfMeasure
            uom_code = uom.Code if uom else None
            print(f'  {code}: {inner.Value} {uom_code}')
        elif inner.GetType().Name == 'EnumerationMember':
            print(f'  {code}: {inner.Value} (code {inner.Code})')
        else:
            print(f'  {code}: {inner}')
    except Exception as e:
        print(f'  {wd.Representation.Code}: ERROR {e}')