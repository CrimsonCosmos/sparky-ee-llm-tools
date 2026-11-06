# CAN node: 12 V -> TPS563201 buck -> STM32F103C8 + SN65HVD230

A real system plan built only from recipes. `specs/*.json` are the datasheet numbers a supervisor extracted from the OFFICIAL datasheets, each with its source
(TI TPS563201 and SN65HVD230 datasheets; ST STM32F103x8 datasheet DS5319 Fig. 14 and Table 13). NRST/BOOT0 values are deliberately absent: the datasheet does not
give them, so the board report warns instead of inventing them (add them from ST's hardware-development application note).

```bash
mkdir -p $SPARKY_HOME/specs && cp specs/*.json $SPARKY_HOME/specs/
sparky download-parts
sparky build-plan plan.json --out out/can_node
```
