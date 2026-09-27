# board

A USB-C powered ATtiny85 LED blinker: plug it into any USB-C charger or port, the
5.1 k Rd pull-downs on CC1/CC2 tell the source to switch VBUS on, an AP2112K-3.3
LDO makes 3.3 V, and the ATtiny85 blinks two LEDs (PB3, PB4) while a user button
(PB2, with a 10 k pull-up) is available for mode changes. A yellow power LED
shows that 5 V is present. Board is 34 x 22 mm, two copper layers, with a GND
pour on both sides.

## Design notes

Generated facts are in the block below; everything here is my own reasoning about
the design, kept outside the generated block.

**Pin budget was the binding constraint.** The ATtiny85 has six GPIOs and the 2x3
Atmel ISP header takes four of them (PB0=MOSI, PB1=MISO, PB2=SCK, PB5=~RST),
leaving PB3/PB4 for the two blink LEDs and only PB2 for the button. PB2 is
therefore shared between the ISP clock and the button, and it is the pin that also
carries INT0, so the firmware can wake on a button press. The button is wired to
GND with an external 10 k pull-up (not a pull-down), so an idle button leaves the
SCK line high and does not fight the programmer; holding the button *while*
programming will corrupt the clock, so program with the button released. If a
future revision needs a dedicated button pin, move to an SOIC-14 AVR (ATtiny44/84)
or drop the ISP header in favour of a 3-pin debugWIRE pad.

**USB-C connector choice.** The first pass used the full 16-pad
`Connector:USB_C_Receptacle_USB2.0_16P` / HRO TYPE-C-31-M-12. Its KiCad footprint
puts all 16 contacts in a single row at 0.5 mm pitch, so the four VBUS pads are
boxed in by D+/D-/SBU pads that this board does not even use, and the autorouter
could not stitch VBUS pad-to-pad without breaking the 0.2 mm clearance (DRC kept
2 unconnected errors). Since the board carries no USB data, it now uses the
power-only Type-C receptacle (`USB_C_Receptacle_PowerOnly_6P` with footprint
`Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal`, GCT
USB4125 series), whose row is 6 pads with ~1 mm of air between them. VBUS then
routes cleanly and the connector's dead data contacts are not on the board at all.
Trade-off: this board can never be re-used as a USB data device; if that matters,
go back to the 16P part and hand-route the VBUS stitching.

**CC resistors.** R1/R2 are 5.1 k each, one per CC pin to GND: that is the
spec-mandated Rd for a sink, and it is what makes a charger/host close its VBUS
switch. Without them a compliant USB-C source will not power the board at all.
The pair advertises default USB power (500 mA); this board draws roughly 15 mA.

**Power / passives.** C1 10 uF on the LDO input, C2 4.7 uF on its output (both
inside the AP2112's specified range, X5R 0805), C3 100 nF as the MCU decoupling
cap. The LDO's EN is strapped to VIN (pin 4 is the NC pad, left open). LED currents:
470 R on the 3.3 V blink LEDs (~2.8 mA) and 1 k on the 5 V power LED (~2-3 mA).
No input fuse or reverse/over-voltage protection: a USB-C source is current
limited by definition, but a mis-wired 5 V supply on the pad would reach the LDO
directly. There is also no ESD protection on the connector, which is acceptable
for a bench toy and not for a product.

**Layout.** Bands, bottom to top: the USB-C receptacle at the bottom-left edge
with the button and the ISP header along the same edge; the power chain (CC
pull-downs, input cap, LDO, output cap, pull-up) in the row directly above the
connector, so VBUS and 3.3 V leave the connector without crossing signals; the
MCU and its LED resistor/LED chains in the top two rows. The connector's shield
legs are NPTH and the router was kept clear of them. Track widths come from
`board.netclasses.json` (see the table below): VBUS and GND 0.6 mm, signals
0.25 mm. GND is poured on both layers, ~822 mm^2 of copper, with solid pour
connections where thermal relief would have starved a pad.

**DRC status: clean, 0 errors, 0 unconnected** on KiCad 10.0.5 with the JLCPCB
2-layer standard rules (default floors, nothing loosened). 22 warnings remain and
are deliberately not "fixed":
- `lib_footprint_issues` (17) - "the current configuration does not include the
  footprint library X". The container has no `fp-lib-table`, so KiCad cannot match
  the embedded footprints back to a library. The footprints themselves *are* in
  `board.kicad_pcb` and in the Gerbers, so the manufacturing data is complete.
- `silk_edge_clearance` (4) and `silk_overlap` (1) - reference designator text is
  clipped by the board edge (J2, U1) or touches a neighbouring part's silkscreen.
  Purely cosmetic; fixable by hiding or moving the fields in KiCad, which the
  SKiDL flow does not drive.

**How to rebuild.** `run_skidl` on `board.py` regenerates `board.net`; check it
against `expected.netlist.toml` with `check_netlist`; `classify_nets` writes
`board.netclasses.json`; `run_layout` with the positions used here reproduces the
board; `export_gerbers` writes `gerbers/`. Firmware is not part of this repo: any
ATtiny85 sketch that toggles PB3/PB4 and reads PB2 works, fuse-wise the default
8 MHz internal clock and RESET enabled are what this board assumes.


<!-- circuit:summary begin (generated by summarize_project; edits here are replaced) -->
## Parts

| Ref | Value | Footprint |
|---|---|---|
| C1 | 10uF | Capacitor_SMD:C_0805_2012Metric |
| C2 | 4.7uF | Capacitor_SMD:C_0805_2012Metric |
| C3 | 100nF | Capacitor_SMD:C_0805_2012Metric |
| D1 | LED Green | LED_SMD:LED_0805_2012Metric |
| D2 | LED Red | LED_SMD:LED_0805_2012Metric |
| D3 | LED Yellow | LED_SMD:LED_0805_2012Metric |
| J2 | AVR-ISP-6 | Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical |
| R1 | 5.1k | Resistor_SMD:R_0805_2012Metric |
| R2 | 5.1k | Resistor_SMD:R_0805_2012Metric |
| R3 | 10k | Resistor_SMD:R_0805_2012Metric |
| R4 | 470 | Resistor_SMD:R_0805_2012Metric |
| R5 | 470 | Resistor_SMD:R_0805_2012Metric |
| R6 | 1k | Resistor_SMD:R_0805_2012Metric |
| S1 | SW_MEC_5G | Button_Switch_THT:SW_PUSH_6mm |
| U1 | USB_C_Receptacle_GCT_USB4125_6P | Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal |
| U2 | AP2112K-3.3 | Package_TO_SOT_SMD:SOT-23-5 |
| U3 | ATtiny85-20S | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm |

## Net classes

| Net | Class | Tier | Width (mm) | Source | Status |
|---|---|---|---|---|---|
| +3V3 | power_logic | lt_0_5a | 0.25 | jev | accepted |
| CC1 | power_high_current | lt_0_5a | 0.25 | jev (resolver held) | needs_review |
| CC2 | power_high_current | lt_0_5a | 0.25 | jev (resolver held) | needs_review |
| GND | ground | - | 0.6 | rule | accepted |
| ISP_MISO | signal | lt_0_5a | 0.25 | jev | accepted |
| ISP_MOSI | signal | lt_0_5a | 0.25 | jev | accepted |
| ISP_RST | signal | lt_0_5a | 0.25 | jev | accepted |
| ISP_SCK | signal | lt_0_5a | 0.25 | jev | accepted |
| LED1 | signal | lt_0_5a | 0.25 | jev | accepted |
| LED1_R | signal | lt_0_5a | 0.25 | jev | accepted |
| LED2 | signal | lt_0_5a | 0.25 | jev | accepted |
| LED2_R | signal | lt_0_5a | 0.25 | jev | accepted |
| PWR_LED | signal | lt_0_5a | 0.25 | jev | accepted |
| VBUS_5V | power_high_current | 0_5a_to_2a | 0.6 | jev (resolver held) | needs_review |

## Nets awaiting your decision

- **CC1**: resolver held: lowers jev's rating: signal lt_0_5a (power_high_current net accepted as signal); sized 0.25 mm from jev's lt_0_5a
- **CC2**: resolver held: lowers jev's rating: signal lt_0_5a (power_high_current net accepted as signal); sized 0.25 mm from jev's lt_0_5a
- **VBUS_5V**: resolver held: lowers jev's rating: power_high_current lt_0_5a (tier lt_0_5a below jev's 0_5a_to_2a); sized 0.8 mm from jev's 0_5a_to_2a
- **GND**: routed at 0.6 mm, pad-limited, below the 0.8 mm tier
- **VBUS_5V**: routed at 0.6 mm, pad-limited, below the 0.8 mm tier

## Board

Status: DRC clean

- File: `board.kicad_pcb`
- Footprints: 17
- Track segments: 59 on B.Cu, 101 on F.Cu
- Outline (Edge.Cuts): yes
- Copper pours: 2, all filled
- DRC: 0 error(s), 22 warning(s)
- Gerbers: board-B_Cu.gbl, board-B_Mask.gbs, board-B_Paste.gbp, board-B_Silkscreen.gbo, board-Edge_Cuts.gm1, board-F_Cu.gtl, board-F_Mask.gts, board-F_Paste.gtp, board-F_Silkscreen.gto, board-NPTH-drl_map.gbr, board-NPTH.drl, board-PTH-drl_map.gbr, board-PTH.drl, board-job.gbrjob
<!-- circuit:summary end -->
