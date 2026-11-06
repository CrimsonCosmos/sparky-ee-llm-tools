from sparky.recipes import Param, Result

META = dict(name="mounting_holes", summary="Mechanical mounting holes (KiCad MountingHole footprints).", category="mechanical")
PARAMS = [Param("footprint", "string", None, "KiCad MountingHole footprint (size chosen by the enclosure/fastener)", True), Param("count", "number", 4, "number of holes", False)]


def build(ctx, footprint, count):
    for _ in range(int(count)):
        ctx.generic("Mechanical:MountingHole", footprint)
    return Result(calcs={"mounting": f"{int(count)} x {footprint}"})
