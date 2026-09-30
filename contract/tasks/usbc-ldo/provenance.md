# usbc-ldo provenance

## Sources

Datasheet reference circuits and the USB Type-C specification (design
2026-09-29-contract-tasks §2). Nothing from these documents is vendored:
`contract.toml` restates facts in our own words. Every document below was
fetched and read on 2026-09-29; pages are the printed page numbers.

| Document | Publisher | Number / revision | Used for |
|---|---|---|---|
| USB Type-C Cable and Connector Specification | USB-IF | Release 2.0, August 2019 | cc1_rd, cc2_rd, cc_separate |
| AP2112 600mA CMOS LDO Regulator with Enable | Diodes Incorporated | DS39724 Rev. 2-2, June 2017 | ldo_rating, cin, cout |
| AMS1117 1A Low Dropout Voltage Regulator | Advanced Monolithic Systems | none printed (file ds1117.pdf, PDF dated 2009-08-27) | ldo_rating, cin, cout |
| LD1117 Series, low drop fixed and adjustable positive voltage regulators | STMicroelectronics | Rev. 19, December 2005 (Jameco-distributed copy) | ldo_rating, cin, cout |
| XC6220 Series, 1A LDO Voltage Regulator with GreenOperation | Torex Semiconductor | ETR0341-008 | ldo_rating, cin, cout |
| TLV757P 1A, Low IQ, Small-Size, Low-Dropout Regulator | Texas Instruments | SBVS322C, October 2017, revised March 2024 | ldo_rating, cin, cout |
| AP7361C 1A Low Dropout Adjustable and Fixed-Mode Regulator with Enable | Diodes Incorporated | DS37274 Rev. 5-2, October 2020 | ldo_rating, cin, cout |
| NCP1117, NCV1117 1.0 A Low-Dropout Positive Fixed and Adjustable Voltage Regulators | ON Semiconductor (onsemi) | NCP1117/D, copyright 2008; revision not printed; read from a distributor-hosted copy (onsemi's own server refused the download) | ldo_rating, cin, cout |
| LM1117 800-mA, Low-Dropout Linear Regulator | Texas Instruments | SNOS412Q, February 2000, revised January 2023 | ldo_rating, cin, cout |
| MCP1700 Low Quiescent Current LDO | Microchip Technology | DS20001826F | rejected part (ldo_rating mutants) |
| MCP1702 250 mA Low Quiescent Current LDO Regulator | Microchip Technology | DS22008E | rejected part |
| HT73xx Low Power Consumption LDO (HT7333) | Holtek | Rev. 1.30, January 26, 2005; distributor-hosted copy | rejected part |
| LP2985, LP2985A 150mA Low-Noise Low-Dropout Regulator with Shutdown | Texas Instruments | SLVS522S, July 2004, revised May 2025 | rejected part |
| XC6206 product page (Torex USA) | Torex Semiconductor | product page, no document number; the datasheet PDF was refused by the publisher's server to every fetch | rejected part |

## Facts confirmed

| Fact | Document | Page, table/figure |
|---|---|---|
| A sink puts an Rd of 5.1 kΩ, ±20%, from each CC pin to ground | USB Type-C spec R2.0 | Table 4-25 "Sink CC Termination (Rd) Requirements", p. 236 (row: resistor to GND, ±20%, 5.1 kΩ) |
| CC1 and CC2 are terminated independently, each through its own Rd | USB Type-C spec R2.0 | Section 4.5.2.2.3.1 "Unattached.SNK Requirements", p. 170 |
| AP2112 guarantees 600 mA (min) output; 1.0 µF input and output ceramic capacitors | AP2112 datasheet | p. 1 (description, features); p. 2 typical application circuit, Note 4 |
| AMS1117 output current of 1 A; asks for 22 µF solid tantalum on the output | AMS1117 datasheet | p. 1 (features); p. 4 "Stability" (application hints) |
| LD1117 up to 800 mA; 10 µF minimum output capacitor | LD1117 datasheet | p. 1 (features, description) |
| XC6220 output current up to 1 A; recommended output capacitor for 3.0-3.5 V output is 4.7 µF with a 10 µF input capacitor | XC6220 datasheet | p. 1 (general description); p. 9 recommended CL table |
| TLV757P output current 0-1 A; input capacitor 1 µF, output capacitor 1 µF minimum (0.47 µF or larger for stability) | TLV757P datasheet | Section 5.3 Recommended Operating Conditions, p. 4; Input and Output Capacitor Selection, p. 15 |
| AP7361C 1 A output; typical circuit 4.7 µF on input and output; 1 µF input capacitor recommended | AP7361C datasheet | p. 1 (description); p. 2 typical application circuit; p. 4 Recommended Operating Conditions; p. 10 Input Capacitor |
| NCP1117 output current in excess of 1.0 A; characterised with 10 µF input and output | NCP1117 datasheet | p. 1 (description, features); p. 3 electrical characteristics heading |
| LM1117 output current 800 mA; 10 µF tantalum minimum at the output, 10 µF tantalum on the input suggested | LM1117 datasheet | p. 1 (features); Section 9.2.2.1 External Capacitors, pp. 14-15 |
| MCP1700 maximum rated output current 250 mA (200 mA below 2.5 V output) | MCP1700 datasheet | p. 1 (features); Section 5.2 Output, p. 13 |
| MCP1702 250 mA output current (200 mA below 2.5 V output) | MCP1702 datasheet | p. 1 (features) |
| HT7333 maximum output current 250 mA | HT73xx datasheet | p. 1 (features); p. 4 electrical characteristics, IOUT(MAX) row for the 3.3 V type |
| LP2985 up to 150 mA load current | LP2985 datasheet | p. 1 (features, description) |
| XC6206 maximum output current 200 mA (3.0 V type) | Torex USA XC6206 product page | product summary (page, not the datasheet) |

Pin names: the AP2112 datasheet names its pins VIN, GND, EN, NC, VOUT (p. 2,
pin descriptions); the AMS1117 datasheet names them VIN and VOUT (p. 1). KiCad's
AMS1117-3.3 symbol says VI and VO, which is why the net specs match both.

## Supplied vs withheld

| Clause | In the prompt | Withheld | Source (page, table/figure) |
|---|---|---|---|
| usb_conn | "a USB-C receptacle": **supplied** | which footprints count (`USB_C` in the library id) | — |
| ldo_rating | "3.3 V at up to 500 mA", "a linear regulator": **supplied** | which regulators are rated for it (allowlist below) | each regulator's datasheet, page 1 |
| cc1_rd, cc2_rd | nothing | Rd = 5.1 kΩ ±20% (4.1k-6.1k) on each CC pin | USB Type-C spec R2.0, Table 4-25, p. 236 |
| cc_separate | nothing | each CC pin has its own Rd | USB Type-C spec R2.0, §4.5.2.2.3.1, p. 170 |
| vbus_to_ldo, ldo_out | the power path, implied | — | — |
| cin, cout | nothing | >= 1 µF within 3 mm of each regulator pin | regulator datasheets (see Widened ranges) |
| rails_distinct | nothing | 5 V and 3.3 V are separate nets | — |

## Allowlists

`ldo_rating` passes regulators whose datasheet rates them for at least
500 mA output: the AP2112, AMS1117, LD1117, XC6220, TLV757, AP7361, NCP1117
and LM1117 families. A failure means "the agent chose a part outside our
list", which may be a defensible choice (a switching regulator, or a
regulator we did not list), not necessarily an error.

The parts the test file rejects (MCP1700, MCP1702, HT7333, XC6206, LP2985)
are each rated 150-250 mA in the documents above.

## Widened ranges

- `cc1_rd`, `cc2_rd`: 4.1k-6.1k, the specification's ±20% around 5.1k.
- `cin`, `cout`: 1 µF is the floor every listed regulator needs. Parts that
  ask for more still pass at 1 µF:
  - AMS1117: 22 µF solid tantalum on the output (p. 4); no input figure.
  - LD1117: 10 µF minimum output (p. 1).
  - NCP1117: 10 µF input and output in its characterisation (p. 3).
  - LM1117: 10 µF tantalum minimum output, 10 µF input suggested (pp. 14-15).
  - XC6220: 4.7 µF output with a 10 µF input for a 3.3 V output (p. 9).
  - AP7361C: 4.7 µF typical at both pins (p. 2).
  - AP2112 (1 µF) and TLV757P (1 µF in, 1 µF out) match the floor.
- `cin`, `cout`: 3 mm, "next to the pin" for a 0603 or 0805 capacitor.

## Known limits

- The regulator's input and output pins are matched by any of three
  spellings KiCad's regulator symbols use: VIN/VOUT, VI/VO and IN/OUT.
- A board with two regulators is graded on both regulators' pins together.
- The output header is found as the two-pad `J*`; a board that brings the
  output out on a larger connector fails `ldo_out`.
- The XC6206's rating rests on the manufacturer's product page, not its
  datasheet, which could not be fetched. It only backs a test-file rejection
  and no `contract.toml` value.
