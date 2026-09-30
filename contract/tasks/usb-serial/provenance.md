# usb-serial provenance

## Sources

Datasheets, a published reference schematic and the USB Type-C specification
(design 2026-09-29-contract-tasks §2). Nothing from these documents is
vendored: `contract.toml` restates facts in our own words. Every document
below was fetched and read on 2026-09-29; pages are the printed page numbers.

| Document | Publisher | Number / revision | Used for |
|---|---|---|---|
| USB Type-C Cable and Connector Specification | USB-IF (fetched from usb.org) | Release 2.0, August 2019 | cc1_rd, cc2_rd, cc_separate |
| USB to Serial Port Chip CH340 Datasheet (I) | WCH (Nanjing Qinheng Microelectronics) | Version 3B (file CH340DS1.PDF, 15 pages); read from a SparkFun-hosted copy (docs.sparkfun.com), because wch-ic.com serves only an HTML download page to scripted fetches. WCH's site lists a newer version 3.4 (2025-03-12) that was not read | bridge, v3_cap, vcc_cap, dp, dm, tx_out, rx_in, dtr_reset |
| Arduino UNO Rev3 schematic | Arduino | file A000066-schematics.pdf from docs.arduino.cc; no revision or date is printed on the sheet beyond the title "Arduino(TM) UNO Rev3" | dtr_reset |

## Facts confirmed

| Fact | Document | Page, table/figure |
|---|---|---|
| A sink puts an Rd of 5.1 kΩ, ±20%, from each CC pin to ground | USB Type-C spec R2.0 | Table 4-25 "Sink CC Termination (Rd) Requirements", p. 236 (row: resistor to GND, ±20%, 5.1 kΩ) |
| CC1 and CC2 are terminated independently, each through its own Rd | USB Type-C spec R2.0 | Section 4.5.2.2.3.1 "Unattached.SNK Requirements", p. 170 |
| CH340C, N, K, E, X and B have an integrated clock and need no crystal; CH340G, T and R need a 12 MHz crystal | CH340 datasheet v3B | p. 1 (note under the part table); §5.1 Clock, reset, power, connect, p. 4 |
| CH340C (SOP-16, pin 13) and CH340K (ESSOP-10, pin 4) have a DTR# output; the CH340N (SOP-8) and CH340E (MSOP-10) columns read NONE for DTR# | CH340 datasheet v3B | §4 Pin definitions, DTR# row, p. 4 |
| With a 5 V supply, VCC takes 5 V and V3 takes a 0.1 µF decoupling capacitor to ground | CH340 datasheet v3B | §4 Pin definitions, V3 row, p. 3; §5.1, p. 4 |
| VCC needs an external 0.1 µF decoupling capacitor | CH340 datasheet v3B | §4 Pin definitions, VCC row, p. 3 |
| Reference circuit: C8 (0.1 µF) on V3, C9 (0.1 µF) on the supply, both to be placed close to the chip; no series resistors on UD+/UD- | CH340 datasheet v3B | §7.1 Applications, p. 9 (C8/C9 text); p. 3 (UD+/UD- note) |
| Pin names TXD, RXD, UD+, UD-, V3, VCC, DTR# | CH340 datasheet v3B | §4 Pin definitions, pp. 3-4 |
| DTR reaches RESET through a 100 nF series capacitor (C5) with a 10 kΩ pull-up on RESET | Arduino UNO Rev3 schematic | sheet 1, ATmega16U2 / reset area: C5 100n between DTR and RESET-EN, then RESET |

## Supplied vs withheld

| Clause | In the prompt | Withheld | Source (page, table/figure) |
|---|---|---|---|
| bridge | "a CH340C USB-to-serial bridge": **supplied** | that a CH340K is also accepted (allowlist below) | CH340 datasheet v3B, p. 1, p. 4 |
| header pin roles | pin 1 ground, 3 5 V, 4 data from adapter, 5 data to adapter, 6 reset: **supplied**, by number and without TX/RX labels, because those labels mean opposite things from the adapter's and the target's side | — | — |
| cc1_rd, cc2_rd | nothing | Rd = 5.1 kΩ ±20% (4.1k-6.1k) on each CC pin | USB Type-C spec R2.0, Table 4-25, p. 236 |
| cc_separate | nothing | each CC pin has its own Rd | USB Type-C spec R2.0, §4.5.2.2.3.1, p. 170 |
| dp, dm | "an ordinary USB host port" | UD+ to D+ and UD- to D- | CH340 datasheet v3B, §4, p. 3 |
| v3_cap | nothing | a capacitor at V3, 10 nF or more, within 3 mm | CH340 datasheet v3B, §4 V3 row p. 3; §7.1 p. 9 |
| vcc_cap | nothing | a capacitor at VCC, 100 nF or more, within 3 mm | CH340 datasheet v3B, §4 VCC row p. 3; §7.1 p. 9 |
| tx_out, rx_in | which header pin is which direction: **supplied** | TXD to pin 4, RXD to pin 5 (the adapter's TXD is the target's input, so TXD goes to the pin the prompt calls data from the adapter) | CH340 datasheet v3B, §4, pp. 3-4 |
| dtr_reset | "a reset pulse to the target ... when the host opens the serial port": **supplied** | a series capacitor between DTR and pin 6, 10-220 nF | Arduino UNO Rev3 schematic, sheet 1, C5 |

## Allowlists

`bridge` passes the CH340C and CH340K only. They are the two variants with an
integrated clock and a DTR output (datasheet v3B, pp. 1 and 4). The CH340E and
CH340N have no DTR pin, so they cannot drive the reset the prompt asks for;
the CH340G has DTR but needs an external 12 MHz crystal. A CH340G with its
crystal is a working alternative that fails `bridge` alone.

## Widened ranges

- `cc1_rd`, `cc2_rd`: 4.1k-6.1k, the specification's ±20% around 5.1k.
- `v3_cap`: the datasheet asks for 0.1 µF at V3 (pp. 3, 9). The clause takes 10 nF
  or more, so any ceramic in the usual range passes.
- `vcc_cap`: the datasheet asks for 0.1 µF at VCC (p. 3); the clause floor is
  100 nF, the datasheet value.
- `v3_cap`, `vcc_cap`: 3 mm, "next to the pin" for a 0603 or 0805 capacitor.
  The datasheet says only "close" (p. 9).
- `dtr_reset`: the reference (Arduino UNO Rev3, C5) uses 100 nF. The clause
  accepts 10-220 nF: any capacitor in that range gives the target's reset pin
  a pulse of usable length through its pull-up, and 100 nF spellings such as
  0.1u and 100n/50V all fall inside. The range is our engineering judgement
  around the reference value, not a figure from a document.

## Known limits

- **Series resistors on the serial lines fail `tx_out`/`rx_in`.** A 470 Ω-1 kΩ
  resistor between TXD and pin 4 is common and valid, but the clause asks for
  a direct connection (pinned by `test_series_resistor_on_tx_is_a_known_failure`).
- The receptacle's `D-` pin is matched as the word `D`, because words split on `-`;
  the bridge's `UD-` likewise.
- The header is found as the six-pad `J*`; a board using another connector fails
  the header clauses.
- The datasheet notes that DTR# on the C and K is also read as a configuration
  input during enumeration (p. 5); a wired-through design is not penalised by
  any clause, and the series capacitor is unaffected.
