# relay-driver provenance

## Sources

Manufacturer datasheets and two application notes (design
2026-09-29-contract-tasks §2). Nothing from these documents is vendored:
`contract.toml` restates facts in our own words. Every document below was
fetched and read on 2026-09-29 as a PDF; pages are the printed page numbers.

| Document | Publisher | Number / revision | Used for |
|---|---|---|---|
| AO3400A 30V N-Channel MOSFET | Alpha & Omega Semiconductor (aosmd.com) | Rev 3.1, July 2023 | mosfet |
| IRLML2502PbF HEXFET Power MOSFET | Infineon / International Rectifier (infineon.com) | dated April 24, 2014 (revision history table, p. 9) | mosfet |
| IRLML6344TRPbF HEXFET Power MOSFET | Infineon / International Rectifier (infineon.com) | dated December 19, 2014 (revision history table, p. 10) | mosfet |
| Si2302CDS N-Channel 20 V MOSFET | Vishay Siliconix (vishay.com) | Document Number 68645, S12-2336-Rev. D, 01-Oct-12 | mosfet |
| 2N7002 60 V, 300 mA N-channel Trench MOSFET | Nexperia (assets.nexperia.com) | Rev. 7, 8 September 2011 | mosfet (excluded part) |
| IRF540NPbF | Infineon (infineon.com) | Rev. 2.1, 2026-07-24 | mosfet (excluded part) |
| IRFZ44NPbF | Infineon (infineon.com) | Rev. 2.1, 2026-08-11 | mosfet (excluded part) |
| BS170 / MMBF170 N-Channel Enhancement Mode FET | onsemi (onsemi.com, file mmbf170-d.pdf) | Publication order number MMBF170/D, February 2026, Rev. 9 | mosfet (excluded part) |
| AND8116/D "Integrated Relay/Inductive Load Drivers for Industrial and Automotive Applications" | onsemi (onsemi.com) | May 2015, Rev. 2 | flyback, gate_pulldown, gate_series |
| AN90059 "Power MOSFET gate driver fundamentals" | Nexperia (assets.nexperia.com) | Rev. 1, 22 April 2025 | gate_pulldown, gate_series |

Omron's relay technical guide and coil-surge FAQ (the sources the design
first named for `flyback`) answered HTTP 403 to every scripted fetch, so they
were not read and are not cited; AND8116/D is used instead.

## Facts confirmed

| Fact | Document | Page, table/figure |
|---|---|---|
| AO3400A specifies RDS(on) at VGS = 2.5 V, ID = 3 A: 48 mOhm max | AO3400A Rev 3.1 | p. 1 product summary; p. 2 electrical characteristics table, RDS(ON) row |
| IRLML2502 specifies RDS(on) at VGS = 2.5 V, ID = 3.6 A: 80 mOhm max (45 mOhm max at 4.5 V) | IRLML2502PbF | p. 2, Electrical Characteristics table, RDS(on) row |
| IRLML6344 specifies RDS(on) at VGS = 2.5 V, ID = 4.0 A: 37 mOhm max | IRLML6344TRPbF | p. 1 summary; p. 2, Electrical Characteristics table |
| Si2302CDS specifies RDS(on) at VGS = 2.5 V, ID = 3.1 A: 0.075 Ohm max | Si2302CDS Rev. D | p. 1 product summary; p. 2 specifications table, drain-source on-resistance row |
| 2N7002 specifies RDS(on) at VGS = 10 V (5 Ohm max) and 4.5 V, ID = 75 mA (5.3 Ohm max), nothing lower | Nexperia 2N7002 Rev. 7 | Table 1 quick reference data (p. 1); Table 7 Characteristics, RDSon rows (p. 5) |
| IRF540N specifies RDS(on) at VGS = 10 V only (44 mOhm max, ID = 16 A) | IRF540NPbF Rev. 2.1 | p. 1 summary; p. 2 electrical characteristics table |
| IRFZ44N specifies RDS(on) at VGS = 10 V only (17.5 mOhm max, ID = 25 A) | IRFZ44NPbF Rev. 2.1 | p. 1 summary; p. 2 electrical characteristics table |
| BS170 specifies RDS(on) at VGS = 10 V only (5 Ohm max, ID = 200 mA) | BS170 / MMBF170 Rev. 9 | electrical characteristics table, RDS(ON) row, p. 2 |
| A 3.3 V logic relay driver puts a free-wheeling diode across the relay coil, with the cathode toward +12 V and the anode toward the switch, to clamp the coil's turn-off spike | onsemi AND8116/D Rev. 2 | p. 2, Figure 1 "Typical Discrete Relay Driver" (D2; text: "provides a clamp function to suppress the voltage spike ... during the turn-off") |
| An N-MOSFET relay driver has a 1.0 kOhm resistor in series with the gate and a 300 kOhm resistor from gate to source | onsemi AND8116/D Rev. 2 | p. 2, Figure 2 and the bullet "Bias Resistors (1.0 k in the Gate and 300 k between Gate and Source)" |
| A gate-to-source pull-down keeps the MOSFET off when the drive is faulty or unpowered; a kOhm-range value is usually suitable | Nexperia AN90059 Rev. 1 | section 6 "Protection circuits", p. 25, Fig. 30 |
| The gate drive is referenced to the MOSFET's source, so the logic ground must be common with the source (power) ground for the gate voltage to mean anything | Engineering fact, no document page cited: every RDS(on) figure above is specified at a gate-to-source voltage, and the gate drive is the header's signal against the header's ground | (none; the datasheets' VGS conditions) |
| A series gate resistor sets the switching speed and damps gate-loop oscillation | Nexperia AN90059 Rev. 1 | section 3.3 "Gate resistance", p. 9 |

Disagreements with the brief: none of substance. The brief says the
excluded parts specify RDS(on) "only at VGS >= 4.5 V"; that is exact for the
2N7002 (10 V and 4.5 V) and looser than the sources for the IRF540N, IRFZ44N
and BS170, which specify it at 10 V alone. In every case nothing is given at
or below 2.5 V, so the list is unchanged. No clause states any RDS(on) figure.

## Supplied vs withheld

| Clause | In the prompt | Withheld | Source (page, table/figure) |
|---|---|---|---|
| terminal and header pin roles | terminal 1 +12 V, 2 ground; header 1 ground, 2 control signal (3.3 V logic, high is on), 3 the microcontroller's 3.3 V: **supplied**, by number | — | — |
| relay, mosfet | "a 12 V coil", "N-channel MOSFET", 3.3 V logic: **supplied** | that the MOSFET must be fully on at 3.3 V gate drive, hence the allowlist | datasheets above, RDS(on) rows |
| coil_supply, coil_drain, source_gnd | nothing beyond the roles above | coil from +12 V to the drain, source on ground | AND8116/D, Fig. 1 |
| flyback | nothing: the prompt does not say "diode" | a diode across the coil, cathode on +12 V, anode on the drain | AND8116/D, p. 2, Fig. 1 |
| gate_pulldown | nothing: the prompt does not say "resistor" | a resistor from gate to ground, 10k-1M | Nexperia AN90059 §6, p. 25; AND8116/D Fig. 2 |
| gate_series | nothing | a resistor from the control signal to the gate, 10R-1k | Nexperia AN90059 §3.3, p. 9; AND8116/D Fig. 2 |
| logic_gnd | the header's pin 1 is ground: **supplied** | the logic ground must be common with the power ground | the datasheets' VGS conditions (gate-to-source); engineering fact |
| rails_distinct | two separate supplies are named | the +12 V and logic supply nets must not be joined | prompt structure |

## Allowlists

`mosfet` passes AO3400/AO3400A, IRLML2502, IRLML6344 and Si2302 families. The
criterion is RDS(on) specified at VGS <= 2.5 V in the datasheet, so a 3.3 V gate
drive turns the part fully on. The 2N7002 (down to 4.5 V), IRF540N, IRFZ44N
and BS170 (10 V) do not specify it, so they fail. Any other N-MOSFET whose datasheet specifies RDS(on) at
VGS <= 2.5 V is a defensible alternative that fails `mosfet` alone.

## Widened ranges

- `gate_pulldown` 10k-1M: Nexperia AN90059 §6 (p. 25) asks only for "the kOhm
  range"; onsemi AND8116/D Fig. 2 integrates 300k. The bounds are our
  engineering judgement: 10k keeps the pull-down from loading a 3.3 V GPIO
  noticeably, 1M is still low enough to bleed leakage and gate charge. 10k,
  100k and 1M spellings all fall inside.
- `gate_series` 10 Ohm-1k: AND8116/D Fig. 2 integrates 1.0k; AN90059 §3.3 (p. 9)
  says the gate resistance damps gate-loop oscillation without giving a range.
  The bounds are our engineering judgement: below 10 Ohm damping is negligible,
  above 1k a 3.3 V drive switches too slowly. 100R, 220 and 1k fall inside.

## Known limits

- The ground net is terminal pin 2's net, so a board whose MOSFET source returns
  to ground only through the logic header fails `source_gnd`.
- A diode whose symbol has no K/A pin names fails `flyback`, because its
  orientation cannot be read (pinned by `flyback--unnamed-pins`).
- The terminal is found as the two-pad `J*` and the header as the three-pad
  `J*`; a board using other connectors fails the clauses that name them.
- A snubber or TVS in place of the diode also fails `flyback`, though it can
  protect the MOSFET.
- A drain shorted to +12 V has no clause of its own: `flyback` fails, because
  the diode then has both ends on one net (pinned by
  `flyback--drain-on-supply`).
- `logic_gnd` reads the header's pin 1 by number, like the other header
  clauses; a board using another connector fails it.
