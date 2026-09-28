# keyfob-base provenance

## Source

- Repository: https://github.com/javiergarciasantana/ASK-OOK-RF-Remote
- Commit: `fb332baa1000430cd7a4f55a11f05be89c5e4cbc` (HEAD on 2026-09-27)
- Licence: **none**. The repository has no LICENSE file, so all rights are
  reserved by default. **Nothing from it is vendored here**: no schematic, board,
  BOM, Gerber or text. `contract.toml` records facts about the design
  (dimensions, component value ranges, connectivity) in our own words.

Derivation follows the CONTRACT design spec
(`docs/superpowers/specs/2026-09-27-contract-benchmark-design.md`, §8.1).

## Supplied vs withheld

`prompt.md` carries intent, and `contract.toml` carries the numbers (spec §3.1).

| Clause | In the prompt | Withheld |
|---|---|---|
| envelope | "fits a Golf Mk6 flip-key shell": **supplied**, because no agent can infer a cavity | 37.0 x 23.4 mm, +0.2 mm tolerance |
| coincell | "a single CR2032 coin cell": **supplied** | which holders count (allowlist below) |
| encoder | "EV1527-compatible OOK encoder": **supplied**, since the encoder family sets everything else | which parts count (allowlist below) |
| buttons | "two momentary push buttons" | which switch footprints count |
| led | "an LED lights while transmitting" | that it needs a current limit |
| rosc | nothing | 300k-430k from encoder oscillator to supply (derived from the encoder datasheet) |
| led_limit | nothing | 470R-4.7k in series (any sane limit at 3 V) |
| decoupling | nothing | >= 10 nF within 3 mm of the encoder supply pin |
| btn_inputs | "each sends a different code" | each switch on a distinct data input |
| btn_not_shorted | nothing | no data input tied to supply |
| batt_supply | nothing | holder + reaches encoder supply pin |

## Allowlists (spec §5.1)

These clauses match a set of acceptable parts, not the reference's part. A
failure means "the agent chose a part outside our allowlist", which may be a
defensible choice, not necessarily "the agent was wrong":

- `coincell`: library id contains `2032`.
- `encoder`: value names a 1527/2260/2262/5262/HT12E/HT6P20-family encoder.
- `buttons`: a momentary push/tactile switch: a KiCad `Button_Switch_SMD`/`_THT`
  footprint, or a footprint named `SW_Push`/`Tactile`/`PTS<n>`, in either case
  not a DIP, slide, toggle, reed or SPDT/DPDT/SP3T switch; at least two.
  Connectors (spring contacts, terminal blocks) never count.
- `led`: library id contains `LED`.

Topology clauses find parts by reference designator (`BT*`, `SW*`/`S*`/`BTN*`,
`D*`/`LED*`), so an allowlist miss fails its identity clause alone. The encoder
is the `U*` part with at least one pin, connected or not, named as an encoder
data input (`K0`-`K3`/`D0`-`D3`): a real fob has a second `U*`, the transmitter,
on the same supply, and the encoder's supply, oscillator and data clauses must
not be satisfied by it.

## Widened ranges

- `led_limit` admits 470R-4.7k rather than the reference's 1k: any sane current
  limit for an indicator LED at 3 V.

## Dropped clauses

- **Transmitter part identity** and **crystal frequency**. The seed repository
  contradicts itself: its README narrates a migration to a CMT2110A transmitter
  with a 26 MHz crystal, while its shipped BOM lists an F115 (SYN115) with a
  13.560 MHz crystal. A clause picking either would assert a fact we do not
  know (spec §3.3).
- **Transmitter CLK pin left unconnected**. It depends on the transmitter
  identity above; its checker (`pin_floating`) was cut with it (spec §5).
- **All RF clauses** (meander trace length, LC match topology, pour keepout
  under the antenna, antenna trace width), which are deferred to `keyfob-rf`
  (spec §8).

## Known limit of function matching

Net clauses find pins by pin name (from the netlist's library symbol or the
pad's pin function). Only for a pad whose pin has no name do they fall back to
the name of the pad's net, and a KiCad auto-generated net name (`Net-(...)`,
`unconnected-(...)`) never counts: `Net-(D2-A)` names a designator, not data
input D2. KiCad ships no EV1527 symbol, so an agent that draws a custom symbol
with no pin names, **and** leaves its nets with anonymous or auto-generated
names, fails `rosc`, `decoupling`, `btn_inputs`, `btn_not_shorted` and
`batt_supply`, even if wired correctly. By spec §5.2 this is a clause
failure: a board whose supply cannot be identified has not shown it meets the
requirement.
