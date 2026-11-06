from collections import defaultdict
from skidl import Pin, Part, Alias, SchLib, SKIDL, TEMPLATE

from skidl.pin import pin_types

SKIDL_lib_version = '0.0.1'

_caller = SchLib(tool=SKIDL).add_parts(*[
        Part(**{ 'name':'TPS563201', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'TPS563201'}), 'ref_prefix':'U', 'fplist':['Package_TO_SOT_SMD:SOT-23-6', 'Package_TO_SOT_SMD:SOT-23-6'], 'footprint':'Package_TO_SOT_SMD:SOT-23-6', 'keywords':'step-down dcdc voltage regulator', 'description':'3A Synchronous Step-Down Voltage Regulator, 4.5-17V Input Voltage, 0.76V to 7V Output Voltage, Eco mode, SOT-23-6', 'datasheet':'https://www.ti.com/lit/ds/symlink/tps563201.pdf', 'pins':[
            Pin(num='1',name='GND',func=pin_types.PWRIN,unit=1),
            Pin(num='2',name='SW',func=pin_types.OUTPUT,unit=1),
            Pin(num='3',name='VIN',func=pin_types.PWRIN,unit=1),
            Pin(num='4',name='VFB',func=pin_types.INPUT,unit=1),
            Pin(num='5',name='EN',func=pin_types.INPUT,unit=1),
            Pin(num='6',name='VBST',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'R', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'R'}), 'ref_prefix':'R', 'fplist':[''], 'footprint':'Resistor_SMD:R_0402_1005Metric', 'keywords':'R res resistor', 'description':'Resistor', 'datasheet':'', 'pins':[
            Pin(num='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'L', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'L'}), 'ref_prefix':'L', 'fplist':[''], 'footprint':'Inductor_SMD:L_1210_3225Metric', 'keywords':'inductor choke coil reactor magnetic', 'description':'Inductor', 'datasheet':'', 'pins':[
            Pin(num='1',name='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',name='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'C', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'C'}), 'ref_prefix':'C', 'fplist':[''], 'footprint':'Capacitor_SMD:C_0402_1005Metric', 'keywords':'cap capacitor', 'description':'Unpolarized capacitor', 'datasheet':'', 'pins':[
            Pin(num='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] })])