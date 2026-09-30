# mcu-i2c provenance

## Sources

Manufacturer datasheets and two application notes (design
2026-09-29-contract-tasks §2). Nothing from these documents is vendored:
`contract.toml` restates facts in our own words. Every document below was
fetched as a PDF and read on 2026-09-29; page numbers are the printed page
numbers, which equal the PDF page numbers in all four.

| Document | Publisher (host) | Number / revision | Used for |
|---|---|---|---|
| ATmega48A/PA/88A/PA/168A/PA/328/P megaAVR Data Sheet (Complete) | Microchip (copy hosted by SparkFun, cdn.sparkfun.com) | DS40002061A, 2018 (footer "DS40002061A-page N") | mcu, avcc, crystal, xtal*_load, reset_pullup, speed grade |
| AVR042 "AVR Hardware Design Considerations" | Microchip / Atmel (ww1.microchip.com) | Atmel-2521R-AVR_Hardware_Design_Considerations_AVR042_Application Note-09/2016 | mcu_decoupling, avcc, reset_pullup, xtal*_load |
| BME280 Data sheet | Bosch Sensortec (bosch-sensortec.com) | Cover: revision 1.24, February 2024, document number BST-BME280-DS001-24 (page footers still print BST-BME280-DS001-23, Revision 1.23) | sensor, csb_high, sdo_tied, sensor_decoupling, sda_wired, scl_wired |
| UM10204 "I2C-bus specification and user manual" | NXP (nxp.com) | Rev. 7.0, 1 October 2021 | sda_pullup, scl_pullup |

The Microchip-hosted ATmega328P PDF links resolved to older Atmel-7810 files
on search, so the DS40002061A "Complete" copy on SparkFun's CDN was read
instead; it is the Microchip document, redistributed by a distributor.

## Facts confirmed

| Fact | Document | Page, table/figure |
|---|---|---|
| With several supply/ground pin pairs, every pair needs its own decoupling capacitor, and the capacitor belongs close to the pins (a distant one enlarges the high-current loop) | AVR042 | §2.1, p. 4 Figure 2-1 and its text; p. 5 last paragraph before §2.2 |
| AVCC is decoupled like the digital supply; typical capacitor 100 nF | AVR042 | §2.2, p. 5 |
| AVCC "should be externally connected to VCC, even if the ADC is not used"; with the ADC in use, through a low-pass filter | ATmega328P datasheet | §1.1.7 AVCC, p. 14 |
| The RESET pin has an internal pull-up (Rrst 30-60 kOhm); an external pull-up helps in noisy environments; 4.7 kOhm or larger with an STK600, not below 10 kOhm for debugWIRE | ATmega328P datasheet; AVR042 | datasheet p. 323 DC characteristics, RRST row (30 / 60 kOhm); AVR042 §3, p. 6 text and Figure 3-1 (4.7 kOhm to VCC, 100 nF to GND) |
| Crystal load capacitors C1 = C2, 12-22 pF for crystals of 3-8 MHz and 8-16 MHz | ATmega328P datasheet | §9.3, p. 39, Table 9-3 |
| For standard high-frequency crystals the recommended external capacitor range is 22-33 pF (stray capacitance 5-10 pF assumed) | AVR042 | §5.3, p. 15 |
| At 2.7-5.5 V the maximum clock is 10 MHz, so 8 MHz at 3.3 V is in the safe operating area | ATmega328P datasheet | p. 2 speed grades; §29.3, p. 312 Figure 29-1; p. 313 Table 29-10 |
| CSB tied directly to VDDIO selects I2C; pulling it low selects SPI | BME280 | §6.1, p. 32; §7.2, p. 39 Figure 17 note "A direct connection between CSB and VDDIO is required" |
| SDO to GND gives address 0x76, to VDDIO gives 0x77; SDO must not float | BME280 | §6.2, pp. 32-33; §7.2, p. 39 Figure 17 ("I2C address bit 0, GND: '0'; VDDIO: '1'") |
| 100 nF on VDD and on VDDIO (C1, C2); pull-ups sized to bus timing and load, "a normal value is 4.7 kOhm" | BME280 | §7.2, p. 39 Figure 17 and notes |
| The I2C pull-up has an upper bound set by the rise time and bus capacitance, and a lower bound set by the supply and the 3 mA low-level sink current | NXP UM10204 | §7.1, p. 50, Figures 41 and 42; worked example in §7.2.4, p. 52 |
| SDA is the MCU's PC4 and SCL its PC5 | ATmega328P datasheet | p. 12 pin configuration, PC4 (ADC4/SDA/PCINT12) and PC5 (ADC5/SCL/PCINT13) |

Disagreements with the brief, and what changed:

- The brief cites BME280 document BST-BME280-DS002. The fetched document
  (revision 1.24) is BST-BME280-DS001-24; DS002 was an older number (the
  distributor copy of revision 1.6 carries it). The source wins; the number
  above is the one read.
- The brief says the low-power crystal oscillator section gives 12-22 pF and
  AVR042 recommends 10 kOhm for reset. Confirmed: the datasheet gives 12-22 pF
  (Table 9-3) but AVR042 §5.3 gives 22-33 pF for standard crystals, and AVR042
  Figure 3-1 draws 4.7 kOhm for the reset pull-up (10 kOhm is only the
  debugWIRE lower bound). The clause ranges are unchanged and admit both.
- The brief points the UM10204 pull-up range at 1k-10k. The manual gives no
  single range, only the two limits above; the bounds are our judgement (see
  Widened ranges).

## Supplied vs withheld

| Clause | In the prompt | Withheld | Source (page, table/figure) |
|---|---|---|---|
| mcu, sensor, crystal | "ATmega328P", "Bosch BME280", "8 MHz crystal", 3.3 V: **supplied** | which package/variants are accepted | datasheets above |
| power header | 2-pin 2.54 mm header, pin 1 3.3 V, pin 2 ground: **supplied** | (not graded) | prompt |
| mcu_decoupling | nothing: no "capacitor" or value | a cap of at least 100 nF within 3 mm of every VCC and AVCC pin | AVR042 §2.1-2.2, pp. 4-5 |
| avcc | nothing | AVCC connected to the supply | ATmega328P datasheet §1.1.7, p. 14 |
| reset_pullup | nothing | a resistor from RESET to the supply, 4.7k-100k | AVR042 §3, p. 6; datasheet p. 323 |
| crystal_xtal1, crystal_xtal2 | "an 8 MHz crystal" | the crystal reaches both oscillator pins | datasheet §9.3, Figure 9-2, p. 38 |
| xtal1_load, xtal2_load | nothing | a capacitor from each oscillator pin to ground, 10-33 pF | datasheet p. 39 Table 9-3; AVR042 §5.3, p. 15 |
| sda_pullup, scl_pullup | nothing | a resistor from each line to the supply, 1k-10k | BME280 p. 39 Figure 17; UM10204 §7.1, p. 50 |
| sda_wired, scl_wired | "over I2C" | the sensor's data and clock reach the MCU's SDA and SCL | BME280 p. 38 pin table |
| csb_high, sdo_tied | nothing | CSB on the supply; SDO tied directly to a rail | BME280 §6.1, §6.2, p. 32-33 |
| sensor_decoupling | nothing | a cap of at least 100 nF within 3 mm of every VDD and VDDIO pin | BME280 p. 39 Figure 17 |

## Allowlists

`mcu` passes any part whose value contains `ATmega328` (P or non-P, any
package). `sensor` passes `BME280`; the BMP280 (no humidity, different part)
fails. `crystal` matches a `Y` reference whose value starts with 8 MHz in the
spellings `8MHz`, `8 MHz`, `8.000MHz`, `8M`; 16, 18 and 80 MHz and 8 kHz fail.
The 8 MHz value is not an arbitrary choice: 3.3 V limits the part to 10 MHz.

## Crystal grading

The crystal is graded as presence plus two connections (`crystal_xtal1`,
`crystal_xtal2`, each "every crystal reaches this net"), not as a
two-terminal part between XTAL1 and XTAL2. A 4-pad crystal touches three nets
(XTAL1, XTAL2 and its grounded case pads), which a two-terminal check would
mis-grade. `test_four_pad_crystal_passes` pins this.

## Widened ranges

- `xtal1_load`, `xtal2_load` 10-33 pF: the datasheet recommends 12-22 pF
  (Table 9-3, p. 39); AVR042 §5.3 (p. 15) recommends 22-33 pF. The right value
  depends on the crystal's own specified load and stray capacitance
  (AVR042 §5.2, pp. 13-15, equations 1 and 2), so any capacitor from either published range, plus
  a little slack below, admits a working design. 12p, 18pF, 22p, 27pF fall
  inside; 1n and 100n fail.
- `sda_pullup`, `scl_pullup` 1k-10k: BME280 says a normal value is 4.7 kOhm
  (p. 39); UM10204 §7.1 (p. 50) bounds the value above by the rise time and
  bus capacitance, and below by supply and the 3 mA sink current (about 1 kOhm
  at 3.3 V +/- 10 %). Bounds are our engineering judgement; 1k, 2.2k, 4.7k and
  10k spellings fall inside.
- `reset_pullup` 4.7k-100k: AVR042 draws 4.7 kOhm (Figure 3-1) and warns that
  much lower values fight a programmer; the internal pull-up is 30-60 kOhm
  (p. 323), so an external value far above it adds nothing. Our judgement.
- `mcu_decoupling`, `sensor_decoupling` "at least 100 nF within 3 mm": both
  sources recommend 100 nF at each pin; the 3 mm bound is a placement
  judgement, and any larger capacitor also passes.

## Decoupling clauses

`mcu_decoupling` and `sensor_decoupling` use `each = true`: every VCC and
AVCC pin (and the sensor's VDD and VDDIO) needs a qualifying capacitor within
3 mm. One capacitor may serve two pins it sits within 3 mm of. The reference
board does this: C3 serves both VCC pins 1.6 mm apart, C5 serves VDD and
VDDIO. `mcu_decoupling--one-pin-only` pins that a missing AVCC capacitor
fails.

## Known limits

- SDA and SCL on the MCU are matched through the engine's pin-function table,
  not KiCad's symbol, which names the pins PC4 and PC5.
- SDO tied through a resistor fails `sdo_tied` (pinned by
  `sdo_tied--via-resistor-only`); the datasheet asks for a direct
  connection.
- A net whose unnamed pads' net name contains a rail word (e.g. `SDO_GND`)
  can satisfy `rails` through that name.
