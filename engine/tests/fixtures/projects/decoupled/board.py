# Fixture: the first live Claude Code run (2026-09-28), an ESP32-C6 WiFi
# temperature/humidity sensor. Its MCU subcircuit has two decoupling caps
# (C3, C4) that SKiDL snaps to U2's 3V3 pin and wires to it; SKiDL 2.3.0
# then purged those wires as dangling on the top sheet, leaving C3:1/C4:1
# unconnected on every seed. See the skidl patch in engine/Dockerfile.
"""WiFi temperature/humidity sensor: USB-C powered ESP32-C6 + Sensirion SHTC3."""
from skidl import *

R_FP = "Resistor_SMD:R_0603_1608Metric"
C_FP = "Capacitor_SMD:C_0603_1608Metric"
C_BULK_FP = "Capacitor_SMD:C_0805_2012Metric"
SW_FP = "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2"


@subcircuit
def usb_input(vbus, gnd, dp, dn):
    global NC
    j = Part("Connector", "USB_C_Receptacle_USB2.0_16P", ref="J1",
             footprint="Connector_USB:USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A")
    vbus += j["A4", "A9", "B4", "B9"]
    gnd += j["A1", "A12", "B1", "B12", "SH"]
    dp += j["A6", "B6"]
    dn += j["A7", "B7"]
    NC += j["A8", "B8"]
    # 5.1k pull-downs on CC advertise a UFP sink so a USB-C charger supplies 5 V
    cc1, cc2 = Net("CC1"), Net("CC2")
    cc1 += j["A5"]
    cc2 += j["B5"]
    r1 = Part("Device", "R", value="5.1k", ref="R1", footprint=R_FP)
    r2 = Part("Device", "R", value="5.1k", ref="R2", footprint=R_FP)
    cc1 & r1 & gnd
    cc2 & r2 & gnd


@subcircuit
def regulator(vbus, v33, gnd):
    u = Part("Regulator_Linear", "AMS1117-3.3", ref="U1",
             footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
    u["VI"] += vbus
    u["VO"] += v33
    u["GND"] += gnd
    cin = Part("Device", "C", value="10uF", ref="C1", footprint=C_BULK_FP)
    cout = Part("Device", "C", value="22uF", ref="C2", footprint=C_BULK_FP)
    vbus & cin & gnd
    v33 & cout & gnd


@subcircuit
def mcu(v33, gnd, dp, dn, sda, scl, led_drv):
    global NC
    u = Part("RF_Module", "ESP32-C6-MINI-1", ref="U2",
             footprint="RF_Module:ESP32-C6-MINI-1")
    u["3V3"] += v33
    u[[1, 2, 11, 14] + list(range(36, 54))] += gnd
    u["IO13"] += dp
    u["IO12"] += dn
    u["IO22"] += sda
    u["IO23"] += scl
    u["IO21"] += led_drv

    c_bulk = Part("Device", "C", value="10uF", ref="C3", footprint=C_BULK_FP)
    c_hf = Part("Device", "C", value="100nF", ref="C4", footprint=C_FP)
    v33 & c_bulk & gnd
    v33 & c_hf & gnd

    # EN: RC power-on delay plus reset button
    en = Net("EN")
    en += u["EN"]
    r_en = Part("Device", "R", value="10k", ref="R3", footprint=R_FP)
    c_en = Part("Device", "C", value="1uF", ref="C5", footprint=C_FP)
    sw_rst = Part("Switch", "SW_Push", value="RESET", ref="SW1", footprint=SW_FP)
    v33 & r_en & en
    en & c_en & gnd
    en & sw_rst & gnd

    # IO9 strap: high = normal boot, hold BOOT low at reset for download mode
    boot = Net("BOOT")
    boot += u["IO9"]
    r_boot = Part("Device", "R", value="10k", ref="R4", footprint=R_FP)
    sw_boot = Part("Switch", "SW_Push", value="BOOT", ref="SW2", footprint=SW_FP)
    v33 & r_boot & boot
    boot & sw_boot & gnd

    # IO8 must be high at reset for download mode to work
    io8 = Net("IO8_STRAP")
    io8 += u["IO8"]
    r_io8 = Part("Device", "R", value="10k", ref="R5", footprint=R_FP)
    v33 & r_io8 & io8

    NC += u["IO0", "IO1", "IO2", "IO3", "IO4", "IO5", "IO6", "IO7", "IO14", "IO15",
            "IO18", "IO19", "IO20", "RXD0", "TXD0"]


@subcircuit
def sensor(v33, gnd, sda, scl):
    global NC
    u = Part("Sensor_Humidity", "SHTC3", ref="U3", value="SHTC3",
             footprint="Sensor_Humidity:Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm")
    u["VDD"] += v33
    u["VSS"] += gnd
    u["SDA"] += sda
    u["SCL"] += scl
    NC += u[5]
    c = Part("Device", "C", value="100nF", ref="C6", footprint=C_FP)
    v33 & c & gnd
    r_sda = Part("Device", "R", value="4.7k", ref="R6", footprint=R_FP)
    r_scl = Part("Device", "R", value="4.7k", ref="R7", footprint=R_FP)
    v33 & r_sda & sda
    v33 & r_scl & scl


@subcircuit
def status_led(drive, gnd):
    r = Part("Device", "R", value="1k", ref="R8", footprint=R_FP)
    d = Part("Device", "LED", ref="D1", footprint="LED_SMD:LED_0603_1608Metric")
    led_a = Net("LED_A")
    drive & r & led_a
    led_a += d["A"]
    d["K"] += gnd


vbus = Net("VBUS")
v33 = Net("+3V3")
gnd = Net("GND")
vbus.drive = POWER
gnd.drive = POWER
dp, dn = Net("USB_DP"), Net("USB_DN")
sda, scl = Net("I2C_SDA"), Net("I2C_SCL")
led_drv = Net("LED_DRV")

usb_input(vbus, gnd, dp, dn)
regulator(vbus, v33, gnd)
mcu(v33, gnd, dp, dn, sda, scl, led_drv)
sensor(v33, gnd, sda, scl)
status_led(led_drv, gnd)

generate_netlist(file_="board.net")
