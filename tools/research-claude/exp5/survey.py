"""EXP-5: SITE0 rail survey (positive shape layers, decap count, port pin counts) for rail selection."""
import mmap, re, collections, json, sys
F = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/data/S4LB002-2Para_260729_1_injected.spd"
with open(F, "rb") as fh:
    d = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    end = d.find(b"* Layer description lines")
    rx = re.compile(rb"^(?:\.Shape (\S+)|(Polygon|PolygonTrace|Circle|Box)\w*::(\S+?)\+ )", re.M)
    layer = None; lay = collections.defaultdict(collections.Counter)
    for m in rx.finditer(d, 0, end):
        if m.group(1):
            layer = m.group(1).decode().replace("pkgshape", ""); continue
        lay[m.group(3).decode()][layer] += 1
    ps = d.find(b"* Port description lines")
    ports = re.findall(rb"(?m)^(Port(\d+)_SITE0::(\S+))\s", d[ps:ps + 200_000_000])
    c0 = d.find(b".Connect ")
    conns = collections.Counter()
    for m in re.finditer(rb"(?m)^\.Connect (\S+) (CAP\S*)[^\n]*\n1 \$Package\.\S+?::(\S+)\n2 \$Package\.\S+?::(\S+)", d, c0):
        n1, n2 = m.group(3).decode(), m.group(4).decode()
        net = n1 if n2 == "DGND" else n2 if n1 == "DGND" else None
        if net:
            conns[net] += 1
out = []
for full, idx, net in ports:
    net = net.decode()
    out.append(dict(port=full.decode().split("::")[0], index=int(idx), net=net, decaps=conns.get(net, 0), layers=dict(lay.get(net, {}))))
for o in out:
    print(o["index"], o["net"], "decaps", o["decaps"], o["layers"])
json.dump(out, open("/home/claude/work/exp5/survey_" + ("260729" if "0729" in F else "260804") + ".json", "w"), indent=1)
