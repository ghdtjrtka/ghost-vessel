"""Cut a multi-emotion take into individual loop segments, then web-encode them.

Image-to-video models are usually prompted to run several expressions in one clip:
[neutral -> emotion A -> neutral -> emotion B -> neutral -> emotion C -> neutral].
Cutting at the neutral "valleys" yields segments that each start and end neutral,
so they drop straight into the player as [neutral -> emotion -> neutral] loops.

Usage:
  # 1) Contact sheet (a frame every 0.3s) to eyeball where the neutral valleys are
  python tools/cut_emotions.py --video take.mp4 --strip
  # 2) Cut at those valley timestamps and web-encode
  python tools/cut_emotions.py --video take.mp4 --emotions shy,happy,surprise --cuts 3.4,6.7

Without --cuts the take is split into equal thirds, which is a rough fallback —
cutting on the actual valleys is what makes the segments seamless.
"""
import argparse, os, subprocess, json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW = os.path.join(HERE, "scratchpad", "emotion_cuts")


def probe_dur(path):
    d = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path]))
    return float(d["format"]["duration"])


def filmstrip(video, out):
    # One frame every 0.3s, tiled - so you can eyeball where the neutral valleys land
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", video, "-vf",
                    "fps=1/0.3,scale=140:-1,tile=10x4", "-frames:v", "1", out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[strip] {out}  (0.3s per cell: column index x 0.3 ~= timestamp)")


def web_encode(video, start, end, dst, scale=512):
    # Encode [start,end] as web-friendly H.264 (faststart / yuv420p)
    args = ["ffmpeg", "-y"]
    if start is not None:
        args += ["-ss", str(start)]
    if end is not None:
        args += ["-to", str(end)]
    args += ["-i", video, "-vf", f"scale={scale}:-2", "-c:v", "libx264",
             "-profile:v", "main", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-an", "-crf", "20", dst]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--emotions", default="", help="3 comma-separated names, in cut order")
    ap.add_argument("--cuts", default="", help="2 valley timestamps in seconds, e.g. 3.4,6.7 (default: equal thirds)")
    ap.add_argument("--out", default=REVIEW, help="output dir for the segments")
    ap.add_argument("--strip", action="store_true", help="only build the contact sheet (to find valleys)")
    ap.add_argument("--scale", type=int, default=512)
    args = ap.parse_args()

    vid = args.video if os.path.isabs(args.video) else os.path.join(HERE, args.video)
    if not os.path.exists(vid):
        print("[!] video not found:", vid); return
    dur = probe_dur(vid)
    base = os.path.splitext(os.path.basename(vid))[0]

    if args.strip:
        filmstrip(vid, os.path.join(REVIEW, base + "_strip.png"))
        print(f"    duration {dur:.2f}s")
        return

    emos = [e.strip() for e in args.emotions.split(",") if e.strip()]
    if len(emos) != 3:
        print("[!] --emotions needs 3 names"); return
    if args.cuts:
        t = [float(x) for x in args.cuts.split(",")]
        assert len(t) == 2, "--cuts takes 2 valley timestamps"
        bounds = [(0, t[0]), (t[0], t[1]), (t[1], dur)]
    else:
        s = dur / 3
        bounds = [(0, s), (s, 2 * s), (2 * s, dur)]
        print(f"[i] no --cuts -> equal thirds ({s:.2f}s each). Run --strip and pass --cuts for clean seams.")

    os.makedirs(args.out, exist_ok=True)
    for (a, b), emo in zip(bounds, emos):
        dst = os.path.join(args.out, f"{emo}.mp4")
        web_encode(vid, a, b, dst, args.scale)
        d = probe_dur(dst) if os.path.exists(dst) else 0
        print(f"  {emo:14} [{a:.2f}~{b:.2f}] -> {dst}  ({d:.2f}s)")
    print(f"\nReview these, then copy the ones you keep into your preset: {args.out}")


if __name__ == "__main__":
    main()
