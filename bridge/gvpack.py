"""Ghost Vessel single-file avatar pack (.gvp).

한 파일 컨테이너: 아바타의 모든 에셋(클립 mp4 + manifest/emotion_map/preset.json +
source/cover/role)을 하나로 묶는다. 엔진이 팩 내부의 파일명/매니페스트를 읽어
available emotions를 뽑고, 클립은 메모리에서 서빙한다(디스크에 낱개 mp4로 안 풀림).

**난독화 = 저지책(deterrence), DRM 아님.** 엔진이 오픈소스라 복호화 로직이 공개되므로
능숙한 사용자는 풀 수 있다. 목적: (1) 한 파일 배포, (2) 그냥 열면 클립이 안 보이게
(zip 아님) → 캐주얼 추출/"쪼개진 모습" 차단. 진짜 보호(구매자별 워터마크/라이선스)는
장터 단계에서 서버 게이팅으로.

포맷:  MAGIC(4) | idxlen(u32 LE) | XOR(index_json) | XOR(concat blobs)
  index = [{"n": arcname, "o": offset, "l": length}, ...]
"""
from __future__ import annotations
import struct, json, os

MAGIC = b"GVP1"
_KEY = b"ghost-vessel-pack-\xa7v1\x11"   # 고정 스트림키(난독화용, 보호 아님)


def _xor(data: bytes) -> bytes:
    """빠른 스트림 XOR (big-int, C레벨). 큰 mp4도 빠름."""
    if not data:
        return b""
    kl = len(_KEY)
    key = (_KEY * (len(data) // kl + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(len(data), "big")


def pack(files: dict, out_path: str) -> str:
    """files: {arcname(str): bytes} → out_path(.gvp)."""
    index, blobs = [], bytearray()
    for name, data in files.items():
        d = _xor(data)
        index.append({"n": name, "o": len(blobs), "l": len(d)})
        blobs += d
    idx = _xor(json.dumps(index, ensure_ascii=False).encode("utf-8"))
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", len(idx)))
        f.write(idx)
        f.write(bytes(blobs))
    return out_path


def is_pack(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except Exception:
        return False


def read(path: str) -> dict:
    """.gvp → {arcname: bytes} (전부 메모리로 복원). 클립은 여기서 바로 서빙."""
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("not a .gvp pack: " + path)
        (ilen,) = struct.unpack("<I", f.read(4))
        index = json.loads(_xor(f.read(ilen)).decode("utf-8"))
        blobs = f.read()
    return {e["n"]: _xor(blobs[e["o"]:e["o"] + e["l"]]) for e in index}


def list_names(path: str) -> list:
    """복원 없이 팩 내부 파일명만 (빠른 조회용)."""
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("not a .gvp pack")
        (ilen,) = struct.unpack("<I", f.read(4))
        index = json.loads(_xor(f.read(ilen)).decode("utf-8"))
    return [e["n"] for e in index]


if __name__ == "__main__":
    # round-trip 자체검증
    import tempfile
    demo = {"manifest.json": b'{"segments":[{"name":"smile"}]}',
            "web/smile.mp4": os.urandom(1_500_000),
            "role.md": "여름 (Yeoreum)".encode("utf-8")}
    tmp = os.path.join(tempfile.gettempdir(), "_gvp_selftest.gvp")
    pack(demo, tmp)
    back = read(tmp)
    ok = all(back[k] == v for k, v in demo.items()) and set(back) == set(demo)
    print("round-trip:", "OK" if ok else "FAIL", "| names:", list_names(tmp), "| size:", os.path.getsize(tmp))
    os.remove(tmp)
