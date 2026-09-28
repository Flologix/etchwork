"""A small USB-C ATtiny85 blinker written as SKiDL subcircuits (schematic
spec §7.1)."""
from skidl import *

from board_parts import C0805, LED0805, R0805


@subcircuit
def usb_power(vbus, gnd):
    j = Part("Connector", "USB_C_Receptacle_PowerOnly_6P",
             footprint="Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal")
    vbus += j["VBUS"]
    gnd += j["GND"], j["SHIELD"]
    for cc in ("CC1", "CC2"):
        r = Part("Device", "R", value="5.1k", footprint=R0805)
        j[cc] & r & gnd
    c = Part("Device", "C", value="10u", footprint=C0805)
    vbus & c & gnd


@subcircuit
def mcu(vcc, gnd, led_drv):
    global NC
    u = Part("MCU_Microchip_ATtiny", "ATtiny85-20S", footprint="Package_SO:SOIC-8_5.3x5.3mm_P1.27mm")
    vcc += u["VCC"]
    gnd += u["GND"]
    led_drv += u[5]
    NC += u[1], u[2], u[3], u[6], u[7]  # RESET, XTAL1/PB3, XTAL2/PB4, MISO, SCK: unused here
    c = Part("Device", "C", value="100n", footprint=C0805)
    vcc & c & gnd


@subcircuit
def indicator(drive, gnd):
    r = Part("Device", "R", value="330", footprint=R0805)
    d = Part("Device", "LED", footprint=LED0805)
    drive & r & d["A,K"] & gnd


vbus, gnd, led = Net("VBUS"), Net("GND"), Net("LED_DRV")
usb_power(vbus, gnd)
mcu(vbus, gnd, led)
indicator(led, gnd)
generate_netlist(file_="board.net")
