"""USB-C powered ATtiny85 LED blinker -- SKiDL design (board.py).

What the board does
  * USB-C receptacle used as a 5 V sink: 5.1 k Rd pull-downs on CC1 and CC2 make
    a charger / port host close its VBUS switch (default USB power, 500 mA).
  * Power-only 6-pin Type-C receptacle (GCT USB4125 6P): this board has no USB
    data, so the data / SBU contacts are simply not present on the connector.
  * AP2112K-3.3 LDO steps 5 V down to 3.3 V, with a 10 uF input and 4.7 uF
    output ceramic.
  * ATtiny85 in SOIC-8 with a 100 nF decoupling capacitor right on VCC.
  * 6-pin Atmel ISP header (2x3, 2.54 mm) for programming with an AVRISP/USBasp.
  * One push button on PB2 (the INT0 pin) with a 10 k external pull-up, to GND.
  * Two blink LEDs on PB3 and PB4 through 470 R, plus a power LED on 5 V
    through 1 k.

Pin budget: the ATtiny85 has six GPIOs and 2x3 ISP occupies four of them
(PB0=MOSI, PB1=MISO, PB2=SCK, PB5=~RST). PB2 is therefore shared between the
ISP clock and the user button; the button uses a pull-up so it is not fighting
the programmer while idle, and the board is only ever programmed with the
button released.
"""

from skidl import *

# ---------------------------------------------------------------- parts -----
usb = Part("Connector", "USB_C_Receptacle_PowerOnly_6P", ref="U1",
           value="USB_C_Receptacle_GCT_USB4125_6P")
usb.footprint = "Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal"

ldo = Part("Regulator_Linear", "AP2112K-3.3", ref="U2", value="AP2112K-3.3")
ldo.footprint = "Package_TO_SOT_SMD:SOT-23-5"

mcu = Part("MCU_Microchip_ATtiny", "ATtiny85-20S", ref="U3", value="ATtiny85-20S")
mcu.footprint = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"

isp = Part("Connector", "AVR-ISP-6", ref="J2", value="AVR-ISP-6")
isp.footprint = "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical"

btn = Part("Switch", "SW_MEC_5G", ref="S1", value="SW_MEC_5G")
btn.footprint = "Button_Switch_THT:SW_PUSH_6mm"

d1 = Part("Device", "LED", ref="D1", value="LED Green")
d2 = Part("Device", "LED", ref="D2", value="LED Red")
d3 = Part("Device", "LED", ref="D3", value="LED Yellow")
for d in (d1, d2, d3):
    d.footprint = "LED_SMD:LED_0805_2012Metric"

RES = "Resistor_SMD:R_0805_2012Metric"
r_cc1 = Part("Device", "R", ref="R1", value="5.1k")
r_cc2 = Part("Device", "R", ref="R2", value="5.1k")
r_pu = Part("Device", "R", ref="R3", value="10k")
r_led1 = Part("Device", "R", ref="R4", value="470")
r_led2 = Part("Device", "R", ref="R5", value="470")
r_pwr = Part("Device", "R", ref="R6", value="1k")
for r in (r_cc1, r_cc2, r_pu, r_led1, r_led2, r_pwr):
    r.footprint = RES

CAP = "Capacitor_SMD:C_0805_2012Metric"
c_in = Part("Device", "C", ref="C1", value="10uF")
c_out = Part("Device", "C", ref="C2", value="4.7uF")
c_dec = Part("Device", "C", ref="C3", value="100nF")
for c in (c_in, c_out, c_dec):
    c.footprint = CAP

# ----------------------------------------------------------------- nets -----
vbus = Net("VBUS_5V")
gnd = Net("GND")
vcc = Net("+3V3")
cc1 = Net("CC1")
cc2 = Net("CC2")
sck = Net("ISP_SCK")      # PB2: ISP clock + user button (see module docstring)
mosi = Net("ISP_MOSI")    # PB0
miso = Net("ISP_MISO")    # PB1
rst = Net("ISP_RST")      # PB5
led1 = Net("LED1")        # PB3
led2 = Net("LED2")        # PB4
pwr_led = Net("PWR_LED")
led1_r = Net("LED1_R")
led2_r = Net("LED2_R")

vbus.drive = POWER
vcc.drive = POWER
gnd.drive = POWER


# Connections are written out with explicit pin numbers: never assume that
# "the ground pin" is pin 2 just because two-pin passives have it there.


# USB-C: both VBUS pads, both GND pads, shield to GND.
usb["A9"] += vbus
usb["B9"] += vbus
usb["A12"] += gnd
usb["B12"] += gnd
usb["SH"] += gnd

# 5.1 k Rd on each CC line: this is what makes a charger close its VBUS.
usb["A5"] += cc1
usb["B5"] += cc2
r_cc1[1] += cc1
r_cc1[2] += gnd
r_cc2[1] += cc2
r_cc2[2] += gnd

# This is the power-only variant: the connector has no data or SBU contacts.

# LDO: EN strapped to VIN so the regulator is always on, NC pad left open.
ldo[1] += vbus
ldo[2] += gnd
ldo[3] += vbus
ldo[5] += vcc
NC += ldo[4]
c_in[1] += vbus
c_in[2] += gnd
c_out[1] += vcc
c_out[2] += gnd

# MCU supply and decoupling.
mcu[8] += vcc
mcu[4] += gnd
c_dec[1] += vcc
c_dec[2] += gnd

# 6-pin Atmel ISP: 1=MISO 2=VCC 3=SCK 4=MOSI 5=~RST 6=GND
isp[1] += miso
isp[2] += vcc
isp[3] += sck
isp[4] += mosi
isp[5] += rst
isp[6] += gnd

mcu[6] += miso            # PB1
mcu[5] += mosi            # PB0
mcu[1] += rst             # PB5
mcu[7] += sck             # PB2: ISP clock and the button, shared

# Push button to GND with an external pull-up, on PB2 / INT0.
btn[1] += sck
btn[2] += sck
btn[3] += gnd
btn[4] += gnd
r_pu[1] += vcc
r_pu[2] += sck

# Two blink LEDs, active-high from PB3 / PB4 through 470 R.
mcu[2] += led1            # PB3
mcu[3] += led2            # PB4
led1 += r_led1[1]
r_led1[2] += led1_r
led1_r += d1[2]
d1[1] += gnd
led2 += r_led2[1]
r_led2[2] += led2_r
led2_r += d2[2]
d2[1] += gnd

# Power LED straight from 5 V.
r_pwr[1] += vbus
r_pwr[2] += pwr_led
pwr_led += d3[2]
d3[1] += gnd

generate_netlist(file_="board.net")
print("board.py: netlist written to board.net")
