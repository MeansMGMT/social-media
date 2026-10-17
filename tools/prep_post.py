#!/usr/bin/env python3
"""Turn a finished post folder into platform-ready media in this repo.

Usage:
  python3 tools/prep_post.py <src_dir> --slug <slug> --type carousel|reel|image [--date YYYY-MM-DD]

What it does:
  carousel  Every PNG/JPG and MP4 in <src_dir>, sorted by filename (files starting with "_" skipped).
            Stills -> 1080x1350 JPEG q90. Clips -> IG spec MP4. Max 10 items (IG limit).
            Also writes li.pdf (stills only) for a future LinkedIn document post.
  reel      reel.mp4 (or the only MP4) -> IG/FB reel spec MP4, plus cover.jpg if present.
  image     The first still -> one JPEG.

Writes into slides/<date>-<slug>/ and prints the "Media JSON (auto)" cell value for the
Social Posting Calendar. Commit and push afterwards; GitHub Pages serves the files.

Media JSON keys the Make scenario reads:
  ig     list of {"media_type": "IMAGE"|"VIDEO", "image_url"|"video_url": url}  (carousel)
  fb     list of {"type": "url", "url": url}   stills only, FB multi-photo posts cannot hold video
  cover  first still (IG single image post, LinkedIn fallback)
  li     image URL LinkedIn posts (cover for now)
  pdf    LinkedIn document URL (not used by Make yet)
  reel   reel MP4 URL
"""
import argparse, datetime, json, os, subprocess, sys
from PIL import Image

BASE = "https://meansmgmt.github.io/social-media"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STILL = (".png", ".jpg", ".jpeg")
VIDEO = (".mp4", ".mov")


def to_jpg(src, dst, box=(1080, 1350)):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    ratio = w / h
    if not (0.8 - 0.01 <= ratio <= 1.91 + 0.01):
        sys.exit(f"{src}: aspect {w}x{h} is outside Instagram's 4:5 to 1.91:1 range")
    scale = min(box[0] / w, 1440 / w) if w > box[0] else 1
    if scale < 1:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    im.save(dst, "JPEG", quality=90, optimize=True, progressive=False)
    size = os.path.getsize(dst)
    if size > 8 * 1024 * 1024:
        sys.exit(f"{dst}: {size} bytes, over Instagram's 8 MB limit")
    return im


def to_mp4(src, dst, reel=False):
    # Instagram: H.264, AAC 48 kHz 128k, <=5 Mbps video, moov at front, 23-60 fps, width <=1920.
    vf = "scale='min(1080,iw)':-2"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-vf", vf, "-r", "30",
           "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-b:v", "4M", "-maxrate", "4.5M", "-bufsize", "9M", "-g", "60", "-sc_threshold", "0",
           "-c:a", "aac", "-ar", "48000", "-b:a", "128k", "-ac", "2",
           "-movflags", "+faststart", dst]
    subprocess.run(cmd, check=True)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", dst]).decode().strip())
    if dur < 3:
        sys.exit(f"{dst}: {dur:.1f}s, Instagram needs at least 3s")
    if reel and dur > 90:
        print(f"WARNING {dst}: {dur:.1f}s. Facebook reels max out at 90s.", file=sys.stderr)
    mb = os.path.getsize(dst) / 1e6
    if mb > 95:
        sys.exit(f"{dst}: {mb:.0f} MB, over GitHub's 100 MB file limit")
    return dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--type", required=True, choices=["carousel", "reel", "image"])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()

    folder = f"{a.date}-{a.slug}"
    out = os.path.join(REPO, "slides", folder)
    os.makedirs(out, exist_ok=True)
    url = lambda name: f"{BASE}/slides/{folder}/{name}"

    files = sorted(f for f in os.listdir(a.src) if not f.startswith((".", "_")))
    stills = [f for f in files if f.lower().endswith(STILL)]
    videos = [f for f in files if f.lower().endswith(VIDEO)]
    media = {}

    if a.type == "carousel":
        items = [f for f in files if f.lower().endswith(STILL + VIDEO)]
        if not 2 <= len(items) <= 10:
            sys.exit(f"Carousel needs 2 to 10 items, found {len(items)}: {items}")
        ig, fb, jpgs = [], [], []
        for n, f in enumerate(items, 1):
            src = os.path.join(a.src, f)
            if f.lower().endswith(STILL):
                name = f"{n:02d}.jpg"
                to_jpg(src, os.path.join(out, name))
                jpgs.append(os.path.join(out, name))
                ig.append({"media_type": "IMAGE", "image_url": url(name)})
                fb.append({"type": "url", "url": url(name)})
            else:
                name = f"{n:02d}.mp4"
                to_mp4(src, os.path.join(out, name))
                ig.append({"media_type": "VIDEO", "video_url": url(name)})
        if not jpgs:
            sys.exit("Carousel has no stills, so Facebook and LinkedIn would get nothing")
        imgs = [Image.open(p).convert("RGB") for p in jpgs]
        imgs[0].save(os.path.join(out, "li.pdf"), save_all=True, append_images=imgs[1:])
        cover = url(os.path.basename(jpgs[0]))
        media = {"ig": ig, "fb": fb, "cover": cover, "li": cover, "pdf": url("li.pdf")}

    elif a.type == "reel":
        pick = [v for v in videos if v.lower().startswith("reel")] or videos
        if len(pick) != 1:
            sys.exit(f"Reel needs exactly one MP4 (reel.mp4 preferred), found {pick}")
        dur = to_mp4(os.path.join(a.src, pick[0]), os.path.join(out, "reel.mp4"), reel=True)
        media = {"reel": url("reel.mp4"), "duration": round(dur, 1)}
        cov = [s for s in stills if s.lower().startswith("cover")]
        if cov:
            img = Image.open(os.path.join(a.src, cov[0])).convert("RGB")
            img.save(os.path.join(out, "cover.jpg"), "JPEG", quality=90)
            media["cover"] = url("cover.jpg")

    else:
        if not stills:
            sys.exit("Image post needs one PNG or JPG")
        to_jpg(os.path.join(a.src, stills[0]), os.path.join(out, "01.jpg"))
        media = {"cover": url("01.jpg"), "li": url("01.jpg"),
                 "fb": [{"type": "url", "url": url("01.jpg")}]}

    print(f"FOLDER slides/{folder}")
    print("MEDIA_JSON " + json.dumps(media, separators=(",", ":")))


if __name__ == "__main__":
    main()
