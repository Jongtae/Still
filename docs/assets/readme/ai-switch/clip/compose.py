"""Compose the site's AI-switch clip: real screen crops pasted into the CSS shells, 10 fps, 0.2 s dissolves.

Usage: WORK=/tmp/ai-switch-clip node shells.cjs
       WORK=/tmp/ai-switch-clip python3 compose.py <folder with the four recordings> site/assets
The recordings are not committed; manifest.json holds their digests.
"""
import hashlib, json, os, pathlib, subprocess, sys
from PIL import Image
HERE = pathlib.Path(__file__).parent; WORK = pathlib.Path(os.environ.get('WORK', '/tmp/ai-switch-clip'))
SOURCES = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
FPS = 10; LOCALES = ['en', 'ko', 'ja', 'zh-CN']
segs = json.loads((HERE / 'segments.json').read_text())
def source(prefix):
    return next(SOURCES.glob(prefix + '*'))
def frame(prefix, at):
    target = WORK / f'src-{prefix}-{at}.png'  # always re-extracted, so a replaced recording cannot leave stale pixels
    subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(source(prefix)), '-frames:v', '1', '-y', str(target)], check=True)
    return Image.open(target).convert('RGB')
def compose(n, seg, lang):
    im = Image.open(WORK / 'shells' / lang / f'{n:02d}.png').convert('RGB')
    if 'bridge' not in seg:
        crop = frame(seg['src'], seg['at']).crop(seg['crop'])
        scale = min(692 / crop.width, 446 / crop.height)
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
        im.paste(crop, (430 + (692 - crop.width) // 2, 164 + (446 - crop.height) // 2))
    return im
frames = {lang: [] for lang in LOCALES}
for n, seg in enumerate(segs):
    count = round(seg['hold'] * FPS)
    for lang in LOCALES:
        cur = compose(n, seg, lang)
        for j in range(count):
            if n and j < 2: frames[lang].append(Image.blend(frames[lang][-1 - j] if j else frames[lang][-1], cur, [.35, .8][j]))
            else: frames[lang].append(cur)
manifest = {'sources': {p: {'filename': source(p).name.split('-', 1)[1], 'sha256': hashlib.sha256(source(p).read_bytes()).hexdigest()} for p in sorted({s['src'] for s in segs if 'src' in s})},
            'segments': segs, 'fps': FPS, 'outputs': {}}
for lang in LOCALES:
    folder = WORK / 'frames' / lang; folder.mkdir(parents=True, exist_ok=True)
    for p in folder.glob('*.png'): p.unlink()
    for i, im in enumerate(frames[lang]): im.save(folder / f'{i:04d}.png')
    for name, args in ((f'still-ai-switch.{lang}.mp4', ['-c:v', 'libx264', '-crf', '24', '-preset', 'slow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an']),):
        target = OUT / name
        subprocess.run(['ffmpeg', '-v', 'error', '-framerate', str(FPS), '-i', str(folder / '%04d.png'), *args, '-y', str(target)], check=True)
        manifest['outputs'][name] = {'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    poster = OUT / f'still-ai-switch-poster.{lang}.jpg'
    frames[lang][round(segs[0]['hold'] * FPS) + 20].save(poster, quality=86, optimize=True, progressive=True)
    manifest['outputs'][poster.name] = {'bytes': poster.stat().st_size, 'sha256': hashlib.sha256(poster.read_bytes()).hexdigest()}
    print(lang, len(frames[lang]) / FPS, 's', flush=True)
manifest['duration_s'] = len(frames['en']) / FPS
(HERE / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
