# Blind, buried and micro vias (HDI)

## What we do and what we reuse
No router of ours. Freerouting already routes with partial-depth vias if the Specctra DSN lists padstacks that carry copper on only some layers and offers them to the
net class; KiCad's exporter writes only the through via. `pcb/hdi.py` therefore
1. adds one padstack per fab span (`Via[a-b]_dia:drill_um`) and offers them before the through via (the autorouter otherwise always picks the through via),
2. lets KiCad's own SES import place them (it reads the span from the padstack name),
3. restores each via's true type (KiCad's import calls every partial via `buried`),
4. writes per-type via limits as KiCad custom rules (`.kicad_dru`, shipped in the package) so KiCad's DRC judges them,
5. re-reads the copper independently (`hdi.check`): span in the fab's table, type, pad and drill, aspect ratio.
KiCad exports one Excellon file per span.

## The fab's table is an input
`Rules.via_spans` = tuple of `ViaSpan(kind, first, last, dia, drill, annular, max_aspect)`: which layers a via may join, its smallest pad/drill/ring, and the deepest
span-to-drill ratio. The profile `generic-hdi-6l` is ILLUSTRATIVE (to run the tests): replace it with your fab's published capability. Dielectric gaps a span crosses
are capped so the span's depth stays within `drill x aspect`; the other gaps share the rest, and the board gets thinner if every gap is capped (derived, not guessed).

## Verified
6-layer MCU board with micro vias: routes, KiCad DRC clean, span checker clean, drill file hole count/size equals the vias in the copper (gerbonara); a via with a
drill below the span's limit is rejected by both KiCad DRC and our checker. Stackup arithmetic is checked by hand in `tests/test_hdi.py`.

## NOT verified / not done
- Whether a fab will build it: its DFM check, sequential-lamination order, laser vs mechanical drilling, via filling/capping, stacked-micro-via rules.
- Combination with KiCadRoutingTools constraints (diff pairs, length groups, fanout): KRT lays through vias only, so such boards are refused with a message.
- Via-in-pad under fine-pitch BGAs still needs `fab_tier='advanced'` and matching rule overrides.
- Whether HDI routes boards that through-only cannot: see the density experiment in NEXT.md (measured, not assumed).
