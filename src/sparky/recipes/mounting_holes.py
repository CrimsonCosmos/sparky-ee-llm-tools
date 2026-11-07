from sparky.recipes import Param, Result

META = dict(name="mounting_holes", summary="Mechanical mounting holes (KiCad MountingHole footprints).", category="mechanical")
PARAMS = [Param("footprint", "string", None, "KiCad MountingHole footprint (size chosen by the enclosure/fastener)", True), Param("count", "number", 4, "number of holes", False)]


def build(ctx, footprint, count):
    corners = ("tl", "tr", "bl", "br")                       # the placer fixes a part with `corner` at that board corner (Rules.corner_inset_mm in)
    if not 1 <= int(count) <= len(corners):
        raise ValueError("mounting_holes: count must be 1..4 (one per corner)")
    for i in range(int(count)):
        ctx.generic("Mechanical:MountingHole", footprint, corner=corners[i])
    return Result(calcs={"mounting": f"{int(count)} x {footprint}"})
