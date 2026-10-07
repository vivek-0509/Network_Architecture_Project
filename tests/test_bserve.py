#!/usr/bin/env python3
"""
End-to-end tests for bserve.  Author: Vivek Singh Solanki (roll number 24bcs10338).
  Each test maps to a line of the assignment or a
rule in SPEC.md (noted in the docstrings).  The server runs as a real
subprocess on a free port; requests come from tools/bclient.py, a separate
implementation of the spec, or from raw sockets for the malformed cases.

    python3 -m unittest discover -s tests -v
"""

import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bclient  # noqa: E402
from bclient import frame, request_payload, read_frame, REQUEST, RESPONSE, DATA, END_STREAM  # noqa: E402

BSERVE = os.path.join(ROOT, "bserve")
MAX = bclient.MAX_FRAME_SIZE

FILES = {
    "index.html": b"<!doctype html>\n<title>test</title>\n<h1>index</h1>\n",
    "hello world.txt": b"hello\n",
    "empty.txt": b"",
    "sub/index.html": b"<p>sub index</p>\n",
    "big.bin": bytes(range(256)) * 390 + b"tail",     # 99 844 bytes -> 7 DATA frames
}


def start_server(www, *extra):
    proc = subprocess.Popen([sys.executable, BSERVE, www, "0"] + list(extra),
                            stderr=subprocess.PIPE, text=True)
    line = proc.stderr.readline()
    m = re.search(r"port (\d+)", line)
    if not m:
        proc.kill()
        raise RuntimeError("bserve did not start: %r" % line)
    threading.Thread(target=lambda: [None for _ in proc.stderr], daemon=True).start()
    return proc, int(m.group(1))


def hdr(resp, name):
    for n, v in resp.headers:
        if n == name:
            return v
    return None


class BserveTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.www = cls.tmp.name
        for rel, data in FILES.items():
            p = os.path.join(cls.www, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        # a file we may not read -> 403 (skipped when running as root)
        p = os.path.join(cls.www, "secret.txt")
        with open(p, "wb") as f:
            f.write(b"secret")
        os.chmod(p, 0)
        # a symlink that escapes the root -> 403
        os.symlink("/etc", os.path.join(cls.www, "escape"))
        cls.proc, cls.port = start_server(cls.www)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.proc.stderr.close()
        os.chmod(os.path.join(cls.www, "secret.txt"), 0o600)
        cls.tmp.cleanup()

    def client(self, **kw):
        c = bclient.Client("127.0.0.1", self.port, **kw)
        self.addCleanup(c.close)
        return c

    def raw(self, preface=True):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        if preface:
            s.sendall(bclient.PREFACE)
        return s

    def assert_closed(self, sock):
        """The server must have closed: the next read returns EOF."""
        self.assertEqual(sock.recv(1), b"")

    # -- "accept a TCP connection / read one frame / map path / reply" --------

    def test_get_file(self):
        r = self.client().request("GET", "/index.html")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, FILES["index.html"])
        self.assertEqual(hdr(r, "content-type"), "text/html; charset=utf-8")
        self.assertEqual(hdr(r, "content-length"), str(len(FILES["index.html"])))
        for name in ("server", "date", "last-modified", "etag"):
            self.assertIsNotNone(hdr(r, name), name)
        self.assertEqual(r.frames[0].flags & END_STREAM, 0)        # body follows
        self.assertEqual(r.frames[-1].flags & END_STREAM, END_STREAM)

    def test_response_frames_echo_request_id(self):
        r = self.client().request("GET", "/index.html", rid=0x00ABCD)
        self.assertTrue(all(f.rid == 0x00ABCD for f in r.frames))

    def test_large_file_is_chunked(self):
        """SPEC 3: body split into DATA frames of <= 16 384 bytes, END_STREAM on the last only."""
        r = self.client().request("GET", "/big.bin")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, FILES["big.bin"])
        data = [f for f in r.frames if f.type == DATA]
        self.assertEqual(len(data), 7)
        self.assertTrue(all(len(f.payload) <= MAX for f in data))
        self.assertEqual([f.flags & END_STREAM for f in data], [0] * 6 + [END_STREAM])
        self.assertEqual(hdr(r, "content-type"), "application/octet-stream")

    def test_empty_file(self):
        """SPEC 3: zero-length body = END_STREAM on the RESPONSE, no DATA frames."""
        r = self.client().request("GET", "/empty.txt")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, b"")
        self.assertEqual(hdr(r, "content-length"), "0")
        self.assertEqual(len(r.frames), 1)
        self.assertTrue(r.frames[0].flags & END_STREAM)

    def test_directory_serves_index(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/sub/").body, FILES["sub/index.html"])
        self.assertEqual(c.request("GET", "/sub").body, FILES["sub/index.html"])
        self.assertEqual(c.request("GET", "/").body, FILES["index.html"])

    def test_query_string_and_percent_encoding(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/index.html?x=1&y=2").status, 200)
        self.assertEqual(c.request("GET", "/hello%20world.txt").body, b"hello\n")
        self.assertEqual(c.request("GET", "/sub/../index.html").body, FILES["index.html"])

    def test_head(self):
        r = self.client().request("HEAD", "/big.bin")
        self.assertEqual(r.status, 200)
        self.assertEqual(hdr(r, "content-length"), str(len(FILES["big.bin"])))
        self.assertEqual(r.body, b"")
        self.assertEqual(len(r.frames), 1)

    def test_304_not_modified(self):
        c = self.client()
        etag = hdr(c.request("GET", "/index.html"), "etag")
        r = c.request("GET", "/index.html",
                      headers=bclient.DEFAULT_HEADERS + [("if-none-match", etag)])
        self.assertEqual(r.status, 304)
        self.assertEqual(r.body, b"")
        self.assertEqual(hdr(r, "etag"), etag)
        r = c.request("GET", "/index.html",
                      headers=bclient.DEFAULT_HEADERS + [("if-none-match", '"stale"')])
        self.assertEqual(r.status, 200)

    def test_literal_header_names_are_accepted(self):
        """SPEC 4: names outside the static table travel length-prefixed."""
        r = self.client().request("GET", "/index.html",
                                  headers=[("host", "x"), ("accept-language", "en"),
                                           ("x-trace-id", "abc123")])
        self.assertEqual(r.status, 200)

    # -- "404 if it is not there" ----------------------------------------------

    def test_404(self):
        c = self.client()
        r = c.request("GET", "/missing.html")
        self.assertEqual(r.status, 404)
        self.assertEqual(hdr(r, "content-type"), "text/plain; charset=utf-8")
        self.assertTrue(r.body.startswith(b"404"))
        self.assertEqual(c.request("GET", "/nodir/").status, 404)

    def test_403_outside_root(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/../etc/passwd").status, 403)
        self.assertEqual(c.request("GET", "/sub/../../etc/passwd").status, 403)
        self.assertEqual(c.request("GET", "/%2e%2e/etc/passwd").status, 403)
        self.assertEqual(c.request("GET", "/escape/passwd").status, 403)   # symlink out
        if os.geteuid() != 0:
            self.assertEqual(c.request("GET", "/secret.txt").status, 403)

    def test_405_for_other_methods_and_body_is_drained(self):
        c = self.client()
        r = c.request("POST", "/index.html", body=b"x" * (MAX + 10))     # 2 DATA frames
        self.assertEqual(r.status, 405)
        self.assertEqual(hdr(r, "allow"), "GET, HEAD")
        self.assertEqual(c.request("DELETE", "/index.html").status, 405)
        self.assertEqual(c.request("GET", "/index.html").status, 200)    # still usable

    # -- "400 if the frame is malformed" ----------------------------------------

    def test_400_undefined_method_keeps_connection(self):
        """SPEC 6: malformed payload -> 400, connection stays open."""
        c = self.client()
        bad = b"\x99" + struct.pack("!H", 1) + b"/" + b"\x00"
        c.send(frame(REQUEST, END_STREAM, 1, bad))
        r = c.read_response(1)
        self.assertEqual(r.status, 400)
        self.assertIn(b"method 0x99", r.body)
        self.assertEqual(c.request("GET", "/index.html", rid=2).status, 200)

    def test_400_truncated_payload(self):
        c = self.client()
        bad = b"\x01" + struct.pack("!H", 100) + b"/abc"          # path-length lies
        c.send(frame(REQUEST, END_STREAM, 5, bad))
        self.assertEqual(c.read_response(5).status, 400)

    def test_400_trailing_bytes(self):
        c = self.client()
        c.send(frame(REQUEST, END_STREAM, 6, request_payload("GET", "/", []) + b"junk"))
        self.assertEqual(c.read_response(6).status, 400)

    def test_400_bad_header_index_and_bad_name(self):
        c = self.client()
        body = b"\x01" + struct.pack("!H", 1) + b"/" + b"\x01" + b"\x0b" + struct.pack("!H", 1) + b"x"
        c.send(frame(REQUEST, END_STREAM, 7, body))                  # index 11 > table
        self.assertEqual(c.read_response(7).status, 400)
        body = b"\x01" + struct.pack("!H", 1) + b"/" + b"\x01" + b"\x00\x04Host" + struct.pack("!H", 1) + b"x"
        c.send(frame(REQUEST, END_STREAM, 8, body))                  # uppercase name
        self.assertEqual(c.read_response(8).status, 400)

    def test_400_path_without_slash(self):
        c = self.client()
        self.assertEqual(c.request("GET", "index.html").status, 400)

    def test_400_request_id_zero(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/index.html", rid=0).status, 400)

    def test_400_stray_data_frame(self):
        c = self.client()
        c.send(frame(DATA, END_STREAM, 9, b"orphan"))
        self.assertEqual(c.read_response(9).status, 400)
        self.assertEqual(c.request("GET", "/index.html").status, 200)

    def test_400_response_frame_from_client(self):
        c = self.client()
        c.send(frame(RESPONSE, END_STREAM, 3, b"\x00\xc8\x00"))
        self.assertEqual(c.read_response(3).status, 400)

    def test_400_bad_preface_closes(self):
        """SPEC 1/6: not our protocol -> 400 and close."""
        s = self.raw(preface=False)
        s.sendall(b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n")
        f = read_frame(s)
        self.assertEqual(f.type, RESPONSE)
        self.assertEqual(struct.unpack("!H", f.payload[:2])[0], 400)
        read_frame(s)                                   # the text/plain body
        self.assert_closed(s)

    def test_400_oversize_frame_closes(self):
        """SPEC 2/6: Length > 16 384 is a framing error -> 400 and close."""
        s = self.raw()
        s.sendall(struct.pack("!II", (MAX + 1) << 8 | REQUEST, END_STREAM << 24 | 4))
        f = read_frame(s)
        self.assertEqual(struct.unpack("!H", f.payload[:2])[0], 400)
        self.assertEqual(f.rid, 4)
        read_frame(s)
        self.assert_closed(s)

    def test_truncated_frame_closes_quietly(self):
        s = self.raw()
        s.sendall(frame(REQUEST, END_STREAM, 1, request_payload("GET", "/", []))[:-3])
        s.close()
        time.sleep(0.1)
        self.assertEqual(self.client().request("GET", "/index.html").status, 200)

    # -- "a receiver meeting a frame type it does not know MUST skip it" ---------

    def test_unknown_frame_type_is_skipped(self):
        """SPEC 2, the rule: unknown types are consumed and ignored."""
        c = self.client()
        c.send(frame(0x42, 0x00, 1, b"\xde\xad\xbe\xef\x00\x01\x02"))
        c.send(frame(0xFF, 0xFF, 0, b""))                  # even id 0, even all flags
        c.send(frame(0x00, 0x00, 9, bytes(MAX)))           # a full-size one
        r = c.request("GET", "/index.html", rid=2)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, FILES["index.html"])

    # -- "and keep the connection open" ----------------------------------------

    def test_keep_alive_many_requests_one_connection(self):
        c = self.client()
        for i in range(1, 21):
            r = c.request("GET", "/index.html" if i % 2 else "/missing")
            self.assertEqual(r.status, 200 if i % 2 else 404)
            self.assertTrue(all(f.rid == i for f in r.frames))
        r = c.request("GET", "/big.bin")
        self.assertEqual(r.body, FILES["big.bin"])

    def test_concurrent_connections(self):
        """A connection that is open but silent must not block others."""
        idle = self.client()                                # preface only, no request
        self.assertEqual(self.client().request("GET", "/index.html").status, 200)
        results = []

        def worker():
            c = bclient.Client("127.0.0.1", self.port)
            results.append(c.request("GET", "/big.bin").body == FILES["big.bin"])
            c.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(results, [True] * 8)
        self.assertEqual(idle.request("GET", "/index.html").status, 200)

    def test_client_close_is_clean(self):
        c = self.client()
        c.request("GET", "/index.html")
        c.close()
        self.assertEqual(self.client().request("GET", "/index.html").status, 200)

    def test_idle_connection_is_closed(self):
        """SPEC 1: a server MAY close an idle connection (bserve: --idle, default 30 s)."""
        proc, port = start_server(self.www, "--idle", "0.5")
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(bclient.PREFACE)
            t0 = time.time()
            self.assertEqual(s.recv(1), b"")                    # server closed first
            self.assertGreaterEqual(time.time() - t0, 0.4)
            s.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            proc.stderr.close()

    # -- claims made in README.md / SPEC.md that the tests above do not cover ---

    def test_400_bad_percent_encoding_and_nul(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/%ff.html").status, 400)      # not UTF-8 once decoded
        self.assertEqual(c.request("GET", "/index%00.html").status, 400)  # NUL byte
        self.assertEqual(c.request("GET", "/index.html").status, 200)

    def test_400_path_not_utf8(self):
        c = self.client()
        bad = b"\x01" + struct.pack("!H", 2) + b"/\xff" + b"\x00"
        c.send(frame(REQUEST, END_STREAM, 11, bad))
        r = c.read_response(11)
        self.assertEqual(r.status, 400)
        self.assertIn(b"UTF-8", r.body)

    def test_fragment_is_dropped(self):
        self.assertEqual(self.client().request("GET", "/index.html#top").status, 200)

    def test_unassigned_flag_bits_are_ignored(self):
        """SPEC 2: receivers ignore flag bits they do not know, even on known types."""
        c = self.client()
        c.send(frame(REQUEST, 0xFF, 12, request_payload("GET", "/index.html", [])))
        r = c.read_response(12)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, FILES["index.html"])

    def test_304_star_and_list(self):
        c = self.client()
        etag = hdr(c.request("GET", "/index.html"), "etag")
        for value in ("*", '"other", %s' % etag):
            r = c.request("GET", "/index.html",
                          headers=bclient.DEFAULT_HEADERS + [("if-none-match", value)])
            self.assertEqual(r.status, 304, value)
            self.assertEqual(len(r.frames), 1)

    def test_head_of_error_has_no_body(self):
        r = self.client().request("HEAD", "/missing")
        self.assertEqual(r.status, 404)
        self.assertEqual(r.body, b"")
        self.assertEqual(len(r.frames), 1)
        self.assertTrue(r.frames[0].flags & END_STREAM)

    def test_content_length_matches_body_and_dates_are_http_dates(self):
        c = self.client()
        http_date = re.compile(r"^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$")
        for path in ("/index.html", "/big.bin", "/empty.txt", "/missing"):
            r = c.request("GET", path)
            self.assertEqual(int(hdr(r, "content-length")), len(r.body), path)
            self.assertRegex(hdr(r, "date"), http_date)
            if r.status == 200:
                self.assertRegex(hdr(r, "last-modified"), http_date)
                self.assertRegex(hdr(r, "etag"), r'^"[0-9a-f]+-[0-9a-f]+"$')

    def test_cli_help_and_bad_root(self):
        out = subprocess.run([sys.executable, BSERVE, "--help"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        for flag in ("-v", "--bind", "--idle"):
            self.assertIn(flag, out.stdout)
        out = subprocess.run([sys.executable, BSERVE, "/no/such/dir", "0"], capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("not a directory", out.stderr)


if __name__ == "__main__":
    unittest.main()
