"""Preset bundle validator — the acceptance gate any builder (human or LLM) runs
before shipping a preset.

    python tools/validate_preset.py presets/<id>

Checks structure, manifest/asset integrity, emotion coverage vs emotion_map,
and prints a PASS/FAIL report with actionable errors. Exit code 0 = shippable.
"""
import json, os, sys

REQUIRED_PRESET_KEYS = ["id", "name", "version", "sfw", "avatar", "role", "emotion_map"]
CORE_EMOTIONS = ["happy", "smile", "surprise", "concerned", "angry", "downcast", "neutral"]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: validate_preset.py presets/<id>")
    pdir = os.path.abspath(sys.argv[1])
    errors, warns = [], []

    # preset.json
    pj = os.path.join(pdir, "preset.json")
    if not os.path.isfile(pj):
        sys.exit(f"FAIL: no preset.json in {pdir}")
    try:
        with open(pj, encoding="utf-8") as _f:
            m = json.load(_f)
    except Exception as e:
        sys.exit(f"FAIL: preset.json is not valid JSON: {e}")
    for k in REQUIRED_PRESET_KEYS:
        if k not in m: errors.append(f"preset.json missing key: {k}")
    if m.get("sfw") is not True:
        warns.append("sfw != true - check the policy of wherever you publish this")

    def local(p):
        # isabs() also blocks Windows C:\... escapes (startswith("/") alone is not enough)
        return None if (not p or os.path.isabs(p) or "://" in p) else os.path.join(pdir, p)

    # role
    rp = local(m.get("role"))
    if rp and not os.path.isfile(rp): errors.append(f"role file missing: {m['role']}")

    # emotion_map
    emo = {}
    ep = m.get("emotion_map")
    if isinstance(ep, str):
        epp = local(ep)
        if not epp or not os.path.isfile(epp):
            errors.append(f"emotion_map missing: {ep}")
        else:
            try:
                with open(epp, encoding="utf-8") as _f:
                    emo = json.load(_f)
            except Exception as e: errors.append(f"emotion_map invalid JSON: {e}")
    elif isinstance(ep, dict):
        emo = ep
    if emo:
        emos = set(emo.get("emotions", []))
        ax = emo.get("axis", {})
        missing_axis = [e for e in emos if e not in ax]
        if missing_axis: warns.append(f"{len(missing_axis)} emotion(s) have no axis entry: {missing_axis[:6]}...")
        if "bases" not in emo: warns.append("no `bases` (mood -> idle mapping) - the 3-axis mood system stays off")

    # avatar manifest + files
    av = m.get("avatar", {}) or {}
    mp_ = local(av.get("manifest"))
    seg_names = set()
    if mp_:
        if not os.path.isfile(mp_):
            errors.append(f"avatar manifest missing: {av.get('manifest')}")
        else:
            with open(mp_, encoding="utf-8") as _f:
                man = json.load(_f)
            base = os.path.dirname(mp_)
            for s in man.get("segments", []):
                seg_names.add(s.get("emotion", s["name"]))
                f = os.path.join(base, s["file"].replace("segments/", "segments" + os.sep))
                # manifest file paths are relative to the avatar dir
                f2 = os.path.join(base, *s["file"].split("/"))
                if not (os.path.isfile(f) or os.path.isfile(f2)):
                    errors.append(f"segment file missing: {s['file']}")
            idles = [s for s in man.get("segments", []) if s["kind"] in ("loop", "mood_idle")]
            if not any(s["name"].startswith("alive_idle") for s in idles):
                warns.append("no alive_idle segment - idle falls back to an expression loop")
    sp = local(av.get("source"))
    if sp and not os.path.isfile(sp): errors.append(f"avatar source missing: {av.get('source')}")

    # emotion coverage
    if emo and seg_names:
        missing_core = [e for e in CORE_EMOTIONS if e != "neutral" and e not in seg_names]
        if missing_core: warns.append(f"no segment for core emotion(s): {missing_core}")
        unused = [e for e in emo.get("emotions", []) if e not in seg_names and e not in ("neutral", "groove")]
        if unused: warns.append(f"{len(unused)} emotion(s) in the map have no segment (they fall back to neutral): {unused[:8]}...")

    # font license reminder
    font = m.get("font") or {}
    if font.get("url"):
        fp = local(font["url"])
        if fp and not os.path.isfile(fp): errors.append(f"font file missing: {font['url']}")
        lic = [f for f in os.listdir(os.path.dirname(fp) if fp else pdir)
               if "licen" in f.lower() or "ofl" in f.lower()] if fp else []
        if not lic: warns.append("no font license bundled - ship a redistributable license (OFL etc.) with the font")

    # cover for store
    if not any(os.path.isfile(os.path.join(pdir, c)) for c in ("cover.png", "cover.jpg")):
        warns.append("no cover.png - the preset will show a blank thumbnail")

    print(f"\n=== validate: {os.path.basename(pdir)} ===")
    for e in errors: print("  [ERROR]", e)
    for w in warns:  print("  [warn ]", w)
    if errors:
        print(f"FAIL ({len(errors)} errors, {len(warns)} warnings)"); sys.exit(1)
    print(f"PASS ({len(warns)} warnings)"); sys.exit(0)


if __name__ == "__main__":
    main()
