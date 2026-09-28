"""Two voltage followers on one LM358, powered from a connector: a small
board with a multi-unit part (both amplifier units and the power unit, wired
by pin number). SKiDL's KiCad 10 schematic writer leaves the pins of units 2
and 3 unconnected, so this correct board never gets a matching drawing
(schematic final review, Critical 1)."""
from skidl import *

HEADER4 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
HEADER2 = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"

vcc, gnd = Net("VCC"), Net("GND")
in_a, in_b, out_a, out_b = Net("IN_A"), Net("IN_B"), Net("OUT_A"), Net("OUT_B")

u = Part("Amplifier_Operational", "LM358", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
j_in = Part("Connector_Generic", "Conn_01x04", footprint=HEADER4)
j_out = Part("Connector_Generic", "Conn_01x02", footprint=HEADER2)
c = Part("Device", "C", value="100n", footprint="Capacitor_SMD:C_0805_2012Metric")

vcc += j_in[1], u[8], c[1]
gnd += j_in[2], u[4], c[2]
in_a += j_in[3], u[3]
in_b += j_in[4], u[5]
out_a += u[1], u[2], j_out[1]
out_b += u[7], u[6], j_out[2]

generate_netlist(file_="board.net")
