"""Hand-built Board objects for checker unit tests: the dump JSON shape,
written out by hand, so no test on the host needs pcbnew."""
from contract.board import Board


def fp(ref, value="", lib_id="", pos=(0.0, 0.0), pad_count=2, rot=0.0):
    return {"ref": ref, "value": value, "lib_id": lib_id, "pos": list(pos),
            "rot": rot, "courtyard": [], "pad_count": pad_count}


def pad(ref, number, net, pos=(0.0, 0.0), names=(), layers=("F.Cu",)):
    return {"ref": ref, "number": str(number), "pos": list(pos),
            "layers": list(layers), "net": net, "names": list(names)}


def rect(w, h, x=0.0, y=0.0):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def make(footprints=(), pads=(), outline=None, tracks=(), zones=()):
    return Board({"outline": rect(30, 20) if outline is None else outline,
                  "footprints": list(footprints), "pads": list(pads),
                  "tracks": list(tracks), "zones": list(zones)})
