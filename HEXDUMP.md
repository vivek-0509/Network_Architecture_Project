# Annotated hexdump: one complete request and response

**Vivek Singh Solanki (roll number 24bcs10338)**

Every byte below crossed a real TCP connection between `tools/bclient.py` and
`bserve` serving `./www`. Regenerate with `make hexdump` (dates and etag will change).

```
$ ./bserve ./www 9000
$ python3 tools/bclient.py localhost 9000 /index.html      # headers: host, user-agent, accept, accept-language
```

Each row is one protocol field: **offset** (hex, counted from the first byte sent in
that direction), the **bytes**, the same bytes as **ASCII** (`.` for non-printable),
and what they **mean**. Multi-byte integers are big-endian. Field layouts are from
SPEC.md sections 2 to 4.

## The exchange at a glance

| # | direction | bytes | frame |
|---|-----------|------:|-------|
| 1 | client -> server | 8 | preface `bhttp/1\n` |
| 2 | client -> server | 8 + 68 | REQUEST, id=1, END_STREAM: GET /index.html, 4 headers |
| 3 | server -> client | 8 + 137 | RESPONSE, id=1: 200 OK, 6 headers |
| 4 | server -> client | 8 + 105 | DATA, id=1, END_STREAM: the file bytes |

| | bhttp/1 | HTTP/1.1 text, same headers |
|---|---:|---:|
| request (preface + REQUEST frame) | 84 bytes | 104 bytes |
| response head (RESPONSE frame) | 145 bytes | 212 bytes |
| response body (DATA frame) | 113 bytes | 105 bytes |

The preface is paid once per connection, not per request; the second request on this
connection would cost just the REQUEST frame.

## Client -> server (84 bytes)

```
----- connection preface (sent once, before any frame) -----
0000  62 68 74 74 70 2f 31 0a  bhttp/1.  ASCII "bhttp/1\n": this is bhttp, version 1

----- frame 1: REQUEST   (8-byte header + 68-byte payload) -----
0008  00 00 44                 ..D       Length = 68           payload bytes that follow the header
000b  01                       .         Type = 0x01           REQUEST
000c  01                       .         Flags = 0x01          END_STREAM: last frame of this request/response
000d  00 00 01                 ...       Request ID = 1
0010  01                       .         method = 0x01         GET
0011  00 0b                    ..        path-length = 11
0013  2f 69 6e 64 65 78 2e 68  /index.h  path = "/index.html"
001b  74 6d 6c                 tml       
001e  04                       .         header-count = 4
001f  01                       .         name-index = 1  -> "host" (static table, no name bytes sent)
0020  00 09                    ..        value-length = 9
0022  6c 6f 63 61 6c 68 6f 73  localhos  value = "localhost"
002a  74                       t         
002b  02                       .         name-index = 2  -> "user-agent" (static table, no name bytes sent)
002c  00 0b                    ..        value-length = 11
002e  62 63 6c 69 65 6e 74 2f  bclient/  value = "bclient/1.0"
0036  31 2e 30                 1.0       
0039  03                       .         name-index = 3  -> "accept" (static table, no name bytes sent)
003a  00 03                    ..        value-length = 3
003c  2a 2f 2a                 */*       value = "*/*"
003f  00                       .         name-index = 0  -> literal name follows
0040  0f                       .         name-length = 15
0041  61 63 63 65 70 74 2d 6c  accept-l  name = "accept-language"
0049  61 6e 67 75 61 67 65     anguage   
0050  00 02                    ..        value-length = 2
0052  65 6e                    en        value = "en"
```

## Server -> client (258 bytes)

```
----- frame 2: RESPONSE   (8-byte header + 137-byte payload) -----
0000  00 00 89                 ...       Length = 137          payload bytes that follow the header
0003  02                       .         Type = 0x02           RESPONSE
0004  00                       .         Flags = 0x00          no END_STREAM: more frames follow
0005  00 00 01                 ...       Request ID = 1
0008  00 c8                    ..        status = 200          OK
000a  06                       .         header-count = 6
000b  05                       .         name-index = 5  -> "content-type" (static table, no name bytes sent)
000c  00 18                    ..        value-length = 24
000e  74 65 78 74 2f 68 74 6d  text/htm  value = "text/html; charset=utf-8"
0016  6c 3b 20 63 68 61 72 73  l; chars  
001e  65 74 3d 75 74 66 2d 38  et=utf-8  
0026  06                       .         name-index = 6  -> "content-length" (static table, no name bytes sent)
0027  00 03                    ..        value-length = 3
0029  31 30 35                 105       value = "105"
002c  07                       .         name-index = 7  -> "server" (static table, no name bytes sent)
002d  00 0a                    ..        value-length = 10
002f  62 73 65 72 76 65 2f 31  bserve/1  value = "bserve/1.0"
0037  2e 30                    .0        
0039  08                       .         name-index = 8  -> "date" (static table, no name bytes sent)
003a  00 1d                    ..        value-length = 29
003c  57 65 64 2c 20 30 37 20  Wed, 07   value = "Wed, 07 Oct 2026 08:36:23 GMT"
0044  4f 63 74 20 32 30 32 36  Oct 2026  
004c  20 30 38 3a 33 36 3a 32   08:36:2  
0054  33 20 47 4d 54           3 GMT     
0059  09                       .         name-index = 9  -> "last-modified" (static table, no name bytes sent)
005a  00 1d                    ..        value-length = 29
005c  57 65 64 2c 20 30 37 20  Wed, 07   value = "Wed, 07 Oct 2026 08:21:38 GMT"
0064  4f 63 74 20 32 30 32 36  Oct 2026  
006c  20 30 38 3a 32 31 3a 33   08:21:3  
0074  38 20 47 4d 54           8 GMT     
0079  0a                       .         name-index = 10  -> "etag" (static table, no name bytes sent)
007a  00 15                    ..        value-length = 21
007c  22 31 38 64 63 33 31 39  "18dc319  value = "18dc319c294aa0fe-69"
0084  63 32 39 34 61 61 30 66  c294aa0f  
008c  65 2d 36 39 22           e-69"     

----- frame 3: DATA   (8-byte header + 105-byte payload) -----
0091  00 00 69                 ..i       Length = 105          payload bytes that follow the header
0094  03                       .         Type = 0x03           DATA
0095  01                       .         Flags = 0x01          END_STREAM: last frame of this request/response
0096  00 00 01                 ...       Request ID = 1
0099  3c 21 64 6f 63 74 79 70  <!doctyp  body: 105 bytes of the file, verbatim
00a1  65 20 68 74 6d 6c 3e 0a  e html>.  
00a9  3c 68 74 6d 6c 3e 3c 68  <html><h  
00b1  65 61 64 3e 3c 74 69 74  ead><tit  
00b9  6c 65 3e 62 68 74 74 70  le>bhttp  
00c1  2f 31 3c 2f 74 69 74 6c  /1</titl  
00c9  65 3e 3c 2f 68 65 61 64  e></head  
00d1  3e 0a 3c 62 6f 64 79 3e  >.<body>  
00d9  3c 68 31 3e 48 65 6c 6c  <h1>Hell  
00e1  6f 20 66 72 6f 6d 20 62  o from b  
00e9  73 65 72 76 65 3c 2f 68  serve</h  
00f1  31 3e 3c 2f 62 6f 64 79  1></body  
00f9  3e 3c 2f 68 74 6d 6c 3e  ></html>  
0101  0a                       .         
```

## Checking the arithmetic

* **REQUEST payload** = method 1 + path-length 2 + path 11 + header-count 1 + headers (1+2+9) + (1+2+11) + (1+2+3) + (1+1+15+2+2)
  = 68. The Length field says `000044` = 68. OK.
* **RESPONSE payload** = status 2 + header-count 1 + headers (1+2+24) + (1+2+3) + (1+2+10) + (1+2+29) + (1+2+29) + (1+2+21)
  = 137. The Length field says `000089` = 137. OK.
* **DATA payload** = 105 bytes; `content-length` in the RESPONSE says 105; `wc -c www/index.html` says 105. OK.
* Each **indexed** header name costs 1 byte; each **literal** name costs 2 + its length.
  `host` as index `01` is 1 byte instead of the 6 (`00 04 h o s t`) a literal would take.
* `END_STREAM` is clear on the RESPONSE (a body follows) and set on the DATA frame (last
  frame of request 1). The client knows the body is complete without reading `content-length`.

