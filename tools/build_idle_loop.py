"""Blink-aligned idle-loop builder — turns a raw idle/mood video into a seamless
loop whose cut point hides inside a blink (both endpoints = blink minima).

    python tools/build_idle_loop.py --video path/to/idle_raw.mp4 \
        --out presets/<id>/avatar --name alive_idle [--mood neutral|positive|negative]
        [--window A B]   # skip auto-pick, force frames A..B
        [--pingpong]     # force pingpong instead of straight loop

Method:
  1. MediaPipe eye-openness per frame -> blink minima.
  2. Pick the blink->blink window maximizing eyes-open% + pose match
     (straight loop; the wrap lands on closed eyes = invisible seam).
  3. Fallback: pingpong when no good blink pair exists.
  4. Settle-time analysis vs the preset's source.png (head-frontal moments the
     player waits for before revealing an expression).
  5. Writes segments/<name>.mp4 (+ web H.264) and updates avatar manifest.json.
"""
import argparse, json, math, os, subprocess, sys

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True, help="preset avatar dir (contains manifest.json, source.png)")
    ap.add_argument("--name", default="alive_idle")
    ap.add_argument("--mood", default=None, choices=[None, "neutral", "positive", "negative"])
    ap.add_argument("--window", nargs=2, type=int, default=None)
    ap.add_argument("--pingpong", action="store_true")
    ap.add_argument("--fps", type=float, default=24.0)
    args = ap.parse_args()

    import cv2, numpy as np, mediapipe as mp, imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    fm = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1,
                                         refine_landmarks=True, min_detection_confidence=0.4)
    c = cv2.VideoCapture(args.video); frames = []
    while True:
        ok, f = c.read()
        if not ok: break
        frames.append(f)
    c.release(); n = len(frames)
    if n < 40:
        sys.exit(f"[!] video too short ({n}f)")

    eye, mouth, yaw = [], [], []
    for f in frames:
        r = fm.process(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        if not r.multi_face_landmarks:
            eye.append(np.nan); mouth.append(np.nan); yaw.append(np.nan); continue
        lm = r.multi_face_landmarks[0].landmark
        fh = abs(lm[10].y - lm[152].y) + 1e-6
        eye.append((abs(lm[159].y - lm[145].y) + abs(lm[386].y - lm[374].y)) / 2 / fh)
        mouth.append(abs(lm[13].y - lm[14].y) / fh)
        yaw.append((lm[1].x - (lm[33].x + lm[263].x) / 2) / (abs(lm[33].x - lm[263].x) + 1e-6))
    eye = np.array(eye); mouth = np.array(mouth); yaw = np.array(yaw)

    if args.window:
        A, B = args.window; style = "pingpong" if args.pingpong else "straight"
    else:
        thr = np.nanpercentile(eye, 75) * 0.5
        closed = eye < thr
        mins, j = [], 0
        while j < n:
            if closed[j]:
                k = j
                while k < n and closed[k]: k += 1
                mins.append(int(j + np.argmin(eye[j:k]))); j = k
            else: j += 1
        print(f"{n}f scanned, blink minima: {mins}")
        best = None
        for i in range(len(mins)):
            for k in range(i + 1, len(mins)):
                a, b = mins[i], mins[k]; L = b - a
                if L < 40: continue
                w_open = 1 - np.nanmean(closed[a:b])
                pose_d = abs(np.nan_to_num(yaw[a]) - np.nan_to_num(yaw[b]))
                talk = np.nanmean(np.nan_to_num(mouth[a:b]))
                sc = w_open * 2 - pose_d * 3 - talk * 5 + L / n * 0.3
                if best is None or sc > best[0]: best = (sc, a, b)
        if best and not args.pingpong:
            _, A, B = best; style = "straight"
        else:
            # pingpong fallback: calmest 60f window
            W = min(60, n - 1); sc = None
            for s in range(0, n - W):
                v = np.nanmean(np.nan_to_num(mouth[s:s+W])) + abs(np.nan_to_num(yaw[s]) - np.nan_to_num(yaw[s+W]))
                if sc is None or v < sc[0]: sc = (v, s)
            A, B = sc[1], sc[1] + W; style = "pingpong"
    print(f"loop window [{A}->{B}] style={style}")

    seq = list(range(A, B)) if style == "straight" else list(range(A, B)) + list(range(B - 1, A, -1))
    segdir = os.path.join(args.out, "segments"); os.makedirs(segdir, exist_ok=True)
    h, w = frames[0].shape[:2]
    tmp = os.path.join(segdir, args.name + "_tmp.mp4")
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))
    for i in seq: vw.write(frames[i])
    vw.release()
    dst = os.path.join(segdir, args.name + ".mp4")
    subprocess.run([ff, "-y", "-i", tmp, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-an", "-loglevel", "error", dst])
    os.remove(tmp)

    # settle times vs the preset's neutral source
    settle = []
    src_p = os.path.join(args.out, "source.png")
    if os.path.exists(src_p):
        MODEL = np.array([(0,0,0),(0,-63.6,-12.5),(-43.3,32.7,-26),(43.3,32.7,-26),
                          (-28.9,-28.9,-24.1),(28.9,-28.9,-24.1)], float)
        IDX = [1,152,33,263,61,291]
        def euler(img):
            hh, ww = img.shape[:2]
            r = fm.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            if not r.multi_face_landmarks: return None
            lm = r.multi_face_landmarks[0].landmark
            pts = np.array([(lm[i].x*ww, lm[i].y*hh) for i in IDX], float)
            cam = np.array([[ww,0,ww/2],[0,ww,hh/2],[0,0,1]], float)
            ok, rv, tv = cv2.solvePnP(MODEL, pts, cam, np.zeros((4,1)), flags=cv2.SOLVEPNP_ITERATIVE)
            if not ok: return None
            R,_ = cv2.Rodrigues(rv); sy = math.sqrt(R[0,0]**2 + R[1,0]**2)
            return np.array([math.degrees(math.atan2(R[1,0],R[0,0])),
                             math.degrees(math.atan2(-R[2,0],sy)),
                             math.degrees(math.atan2(R[2,1],R[2,2]))])
        base = euler(cv2.imread(src_p))
        if base is not None:
            devs = []
            for i in seq:
                e = euler(frames[i])
                devs.append(999.0 if e is None else math.sqrt(((e[0]-base[0])*1.3)**2 +
                            ((e[1]-base[1])*0.7)**2 + ((e[2]-base[2])*1.3)**2))
            devs = np.array(devs); below = devs <= devs.min() + 4.0; j = 0
            while j < len(devs):
                if below[j]:
                    k = j
                    while k < len(devs) and below[k]: k += 1
                    settle.append(round((j + int(np.argmin(devs[j:k]))) / args.fps, 3)); j = k
                else: j += 1
    print("settle times:", settle)

    man_p = os.path.join(args.out, "manifest.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else {"segments": []}
    man["segments"] = [s for s in man["segments"] if s["name"] != args.name]
    entry = {"name": args.name, "file": f"segments/{args.name}.mp4", "emotion": "neutral",
             "kind": "mood_idle" if args.mood and args.mood != "neutral" else "loop",
             "group": "mood" if args.mood else "alive", "frames": len(seq), "fps": args.fps,
             "loop_style": style + "_blink_aligned", "settle_times": settle}
    if args.mood: entry["mood"] = args.mood
    man["segments"].append(entry)
    json.dump(man, open(man_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"OK: {dst} ({len(seq)}f, {style}) + manifest updated")


if __name__ == "__main__":
    main()
