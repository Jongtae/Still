"""Rewrite GIF frame delays in place (lossless): hold x2, intertitles x1.4, motion x1.5."""
import json, sys
def gce_offsets(b):
    i = 13
    if b[10] & 0x80: i += 3 * (2 ** ((b[10] & 7) + 1))
    out = []
    while i < len(b):
        t = b[i]
        if t == 0x3B: break
        if t == 0x21:
            if b[i+1] == 0xF9: out.append(i + 4)  # delay field (little-endian, centiseconds)
            i += 2
            while b[i]: i += b[i] + 1
            i += 1
        elif t == 0x2C:
            f = b[i+9]; i += 10
            if f & 0x80: i += 3 * (2 ** ((f & 7) + 1))
            i += 1
            while b[i]: i += b[i] + 1
            i += 1
        else: raise ValueError(f'bad block {t:#x} at {i}')
    return out
src, dst, manifest = sys.argv[1:4]
segs = json.load(open(manifest))['wide_edit']['segments']
b = bytearray(open(src, 'rb').read()); offs = gce_offsets(b)
total = 0
for n, o in enumerate(offs):
    t = n / 10
    seg = next(s for s in segs if s['output_start_s'] - 1e-6 <= t < s['output_start_s'] + s['output_duration_s'] + 1e-6)
    k = 1.4 if 'bridge' in seg else 2.0 if 'hold' in seg else 1.5
    d = round(int.from_bytes(b[o:o+2], 'little') * k); b[o:o+2] = d.to_bytes(2, 'little'); total += d
open(dst, 'wb').write(b); print(dst, len(offs), 'frames', total / 100, 's')
