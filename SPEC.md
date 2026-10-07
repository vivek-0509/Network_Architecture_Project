# bhttp/1: HTTP over binary frames

**Author:** Vivek Singh Solanki (roll number 24bcs10338). **Version 1.** Transport: one TCP connection. Every multi-byte integer is big-endian
(network order). Lengths are in bytes. `u8`/`u16`/`u24` are unsigned integers of that
width. This document is the whole contract between a client (`bcurl`) and a server
(`bserve`); a client written only from this page must work against any conforming server.

## 1. Connection

The client opens a TCP connection and sends the 8-byte **preface**
`62 68 74 74 70 2f 31 0a` (ASCII `bhttp/1\n`) before anything else. A server that does
not receive exactly these 8 bytes first MUST reply with a RESPONSE of status 400 and
close. After the preface, both sides send only **frames** (§2), until the client closes
the connection. The connection carries any number of requests, **one after another**:
the client sends a complete request, reads the complete response, then may send the
next. A server MAY close a connection that has been idle for 30 s. If the connection
closes before a response's last frame, the response is incomplete and the client MUST
treat it as an error.

## 2. Frame header: 8 bytes, fixed

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Length (24)              |   Type (8)    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Flags (8)   |                 Request ID (24)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     Payload (Length bytes) ...
```

| Field | Width | Meaning |
|---|---|---|
| Length | 24 bits | Payload bytes that follow the header. In version 1, 0 ≤ Length ≤ **16 384**. A larger value is a framing error (§6). |
| Type | 8 bits | `0x01` REQUEST, `0x02` RESPONSE, `0x03` DATA. Every other value is *unassigned* (see the rule below). |
| Flags | 8 bits | Bit 0 (`0x01`) is **END_STREAM**: "this is the last frame of this request or response". Other bits are unassigned: senders clear them, receivers ignore them. |
| Request ID | 24 bits | The request this frame belongs to. The client chooses it (start at 1, add 1 per request) and the server echoes it in every frame of the reply. **0 is reserved** for future connection-level frames and is never valid for a request. |

The header is two 32-bit words, so in C it is `w0 = ntohl(..)`, `len = w0 >> 8`,
`type = w0 & 0xff`, `w1 = ntohl(..)`, `flags = w1 >> 24`, `id = w1 & 0xffffff`.

**The rule you may not skip.** A receiver that meets a frame whose Type it does not
know MUST read exactly Length payload bytes, discard them, and continue with the next
frame as though nothing had arrived. It MUST NOT close the connection, reply with an
error, or interpret the payload. This is what lets a later version add frame types
that version-1 peers survive. The Length limit still applies to unknown frames.

## 3. Frame types

**REQUEST** (`0x01`), client → server. Payload:

```
u8   method        0x01 GET  0x02 HEAD  0x03 POST  0x04 PUT  0x05 DELETE  0x06 OPTIONS
u16  path-length
[]   path          path-length bytes of UTF-8; MUST begin with '/'; may carry ?query;
                   percent-encoding is allowed and is decoded by the server
u8   header-count
[]   header fields  header-count of them, encoded as in §4
```

END_STREAM set means the request has no body. Clear means DATA frames with the same
Request ID follow, the last of them carrying END_STREAM.

**RESPONSE** (`0x02`), server → client. Payload:

```
u16  status         the HTTP status code (200, 404, ...)
u8   header-count
[]   header fields  as in §4
```

END_STREAM set means there is no body (HEAD, 304, an empty file). Clear means DATA
frames follow.

**DATA** (`0x03`), either direction. The payload is raw body bytes. A body is the
concatenation of its DATA frames' payloads in order; each is at most 16 384 bytes and
the last carries END_STREAM. A zero-length body needs no DATA frame at all: put
END_STREAM on the REQUEST or RESPONSE instead. **Framing, not `content-length`, ends a
body**; `content-length` is advisory and, if present, MUST equal the total body size.

## 4. Header fields

A header field is a *name* and a *value*. Names are 1-255 bytes of printable ASCII,
lowercase, with no `:`; values are opaque bytes. The first two ideas of HPACK, and
only those: names that are common are sent as a one-byte index into a fixed table;
every other name is sent literally with a length prefix; values are always literal.

```
u8   name-index     1..10: the name is static-table entry n;  0: a literal name follows
u8   name-length    only present when name-index == 0
[]   name           only present when name-index == 0
u16  value-length
[]   value
```

| # | name | # | name | # | name | # | name | # | name |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `host` | 2 | `user-agent` | 3 | `accept` | 4 | `if-none-match` | 5 | `content-type` |
| 6 | `content-length` | 7 | `server` | 8 | `date` | 9 | `last-modified` | 10 | `etag` |

Indexes 11-255 are reserved and MUST NOT be sent in version 1; a receiver treats them
as malformed (§6), since it cannot know what name they stand for. Example:
`host: localhost` is `01 00 09 6c 6f 63 61 6c 68 6f 73 74` (12 bytes, versus 17 as
text); `accept-language: en` is `00 0f` + `accept-language` + `00 02 65 6e` (21 bytes).

## 5. Request, response, and what the server does

```
C → S   preface  "bhttp/1\n"
C → S   REQUEST  id=1  END_STREAM     GET /index.html + headers
S → C   RESPONSE id=1                 200 + headers
S → C   DATA     id=1  END_STREAM     the file (one or more DATA frames, last has END_STREAM)
C → S   REQUEST  id=2  ...            connection stays open
```

`bserve ROOT PORT` maps the path (minus `?query`, percent-decoded, `.`/`..` segments
resolved) onto a file under ROOT. A directory serves its `index.html`.

| Status | When | Body |
|---|---|---|
| 200 | file found; GET sends the bytes, HEAD only the headers | file |
| 304 | request carried `if-none-match` equal to the file's `etag` (or `*`) | none |
| 400 | malformed frame or payload (§6); the body names the problem | text/plain |
| 403 | path climbs out of ROOT (also via symlink), or file not readable | text/plain |
| 404 | nothing at that path | text/plain |
| 405 | method defined but not GET/HEAD; response carries `allow: GET, HEAD` | text/plain |
| 500 | I/O error while serving | text/plain |

Every 200 carries `content-type`, `content-length`, `server`, `date`, `last-modified`
and `etag`. Servers MAY add headers; clients MUST accept names they do not know.

## 6. Errors

Two kinds, distinguished by whether the receiver can still find the next frame boundary.

* **Malformed payload** (frame boundaries intact): undefined method; path not
  beginning with `/` or not UTF-8; a length field running past the payload; bytes
  left over after the header block; header index > 10 or an invalid name; Request ID
  0; DATA for a Request ID with no open request; a defined frame type arriving in the
  wrong direction. The server replies RESPONSE **400** with the offending Request ID
  and **keeps the connection open**; the next request works normally.
* **Malformed frame** (the byte stream cannot be trusted): wrong preface, Length >
  16 384, connection closed in the middle of a frame. The server replies RESPONSE
  **400** (Request ID of the frame it was parsing, else 0) and **closes**.

A client that receives a frame for a Request ID it did not send, or of a type it does
not know, skips it (§2).

## 7. Why these widths, and what version 2 may change

**What HTTP/2 chose, 24/8/8/31 (9 bytes), and why.** *Length 24*: one frame must be
small enough that a single stream cannot hog a multiplexed connection and a receiver
can bound its buffer before validating anything. 16 bits would cap frames at 64 KiB
forever; 32 bits would license a 4 GiB allocation from a 4-byte header. So 24, with a
negotiable limit (default 16 KiB) underneath. *Type 8*: 256 types, ten used; the
smallest addressable unit. *Flags 8*: one byte of per-type booleans (END_STREAM,
END_HEADERS, PADDED, PRIORITY, ACK). *Stream ID 31 + 1 reserved bit*: a whole 32-bit
word; two billion streams split odd/client, even/server; keeping the top bit clear also
means the ID never looks negative in a signed `int32`. Nine bytes, unaligned: frames are
parsed, not memory-mapped, so padding to 12 would buy nothing.

**What bhttp/1 chose, 24/8/8/24 (8 bytes), and why.** Length, Type and Flags are
HTTP/2's, for HTTP/2's reasons; the limit is fixed at 16 KiB in v1 and a later version
can raise it with a new SETTINGS-style frame type, which v1 peers skip by the rule in
§2. The **Request ID is 24 bits, not 31**: v1 has no multiplexing, so it needs neither
odd/even halves nor billions of IDs, 16 million requests per connection is more than
any keep-alive connection will see, and dropping those 8 bits lands the header on
exactly two 32-bit words. **ID 0 is reserved** so connection-level frames have a home
later. There is **no version byte in the header**: "which version?" is asked once per
connection, so the preface answers it once, instead of spending a byte on every frame.
**Status is a 16-bit integer**, not a 1-byte index: the three-digit codes fit, and there
is no table to keep in step with HTTP. **Names are indexed, values are not**: names
repeat on every request, values rarely do; HPACK's remaining mechanisms (dynamic table,
Huffman coding) are where the next bytes would come from, and they are not worth a page.

**Version 2 may**: add frame types (v1 skips them), add flag bits (v1 ignores them),
use Request ID 0 for connection-level frames, raise the frame limit, extend the static
table. It may not change the 8-byte header layout without changing the preface, which
is exactly what the preface is for.
