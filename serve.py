#!/usr/bin/env python3
"""정적 서버 — 플레이어/세그먼트 서빙 (기본 :8777).

`python -m http.server 8777` 대체용. 아바타가 감정 전환마다 영상 소스를 교체하면
브라우저가 이전 클립 요청을 끊는데, 기본 http.server는 그때마다 ConnectionResetError
트레이스백을 콘솔에 쏟는다(무해하지만 시끄러움). 이 서버는 그걸 조용히 무시하고
로그도 끈다.

사용:  python serve.py            # :8777, 레포 루트 서빙
       python serve.py 9000       # 포트 지정
"""
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# 패키지(frozen)에선 GV_ROOT(런처가 설정) 우선, 없으면 exe/스크립트 위치.
ROOT = os.environ.get("GV_ROOT") or (
    os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__)))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):
        pass                                                # 접속 로그 끔

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # 브라우저가 영상 소스 교체하며 요청 취소 — 무해, 조용히 종료
            self.close_connection = True

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass                                            # 스트리밍 중 취소도 무시


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), QuietHandler)
    httpd.daemon_threads = True
    print(f"[static] serving {ROOT}")
    print(f"[static] http://127.0.0.1:{PORT}/player/index.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
