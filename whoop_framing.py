"""WHOOP 5/MG ("puffin") BLE frame codec — pure Python, no dependencies.

Wire contract (reverse-engineered, documented in github.com/ryanbr/noop
docs/PROTOCOL_TRANSPORT.md "Format 1 framing" and Framing.swift). All multi-byte
integers are little-endian.

Request frame (to command-write characteristic fd4b0002):
    [0]     0xAA  start of frame
    [1]     0x01  format
    [2:4]   declared length u16 = len(padded inner) + 4
    [4:6]   header, requests use 00 01
    [6:8]   CRC16/Modbus over bytes [0:6]
    [8]     packet type, 35 = command
    [9]     request sequence (8-bit, wraps)
    [10]    command number
    [11:]   command body, then zero padding so the inner record (from [8]) is a
            multiple of 4 bytes
    [-4:]   CRC32 (zlib) over the padded inner record (from offset 8)
    total = declared length + 8

Response frame (type 36), offsets after the 8-byte envelope:
    [8]=36 type  [9]=response seq  [10]=command  [11]=ORIGINATING request seq
    [12]=result  [13:]=body
Correlate a response to a request by (command, originating request seq).
result: 0 fail, 1 success, 2 pending, 3 unsupported.
"""
from __future__ import annotations
import struct
import zlib

SOF = 0xAA
PACKET_TYPE_COMMAND = 35
PACKET_TYPE_RESPONSE = 36

RESULT_NAMES = {0: "FAILURE", 1: "SUCCESS", 2: "PENDING", 3: "UNSUPPORTED"}

# CRC-8, poly 0x07, init 0 (table ported from Framing.swift).
_CRC8 = []
for _b in range(256):
    c = _b
    for _ in range(8):
        c = ((c << 1) ^ 0x07) & 0xFF if (c & 0x80) else ((c << 1) & 0xFF)
    _CRC8.append(c)


def crc8(data: bytes) -> int:
    c = 0
    for x in data:
        c = _CRC8[c ^ x]
    return c


def crc16_modbus(data: bytes) -> int:
    """CRC-16/Modbus: poly 0xA001 (reflected), init 0xFFFF."""
    crc = 0xFFFF
    for x in data:
        crc ^= x
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def crc32(data: bytes) -> int:
    """Standard zlib/reflected CRC-32 (poly 0xEDB88320)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build_command_frame(cmd: int, seq: int, body: bytes = b"",
                        ptype: int = PACKET_TYPE_COMMAND,
                        header: bytes = b"\x00\x01") -> bytes:
    """Build a Format-1 command frame. Mirrors puffinCommandFrame()."""
    inner = bytes([ptype, seq & 0xFF, cmd & 0xFF]) + bytes(body)
    pad = (4 - len(inner) % 4) % 4
    inner += b"\x00" * pad
    decl = len(inner) + 4
    head = bytes([SOF, 0x01, decl & 0xFF, (decl >> 8) & 0xFF, header[0], header[1]])
    c16 = crc16_modbus(head)
    frame = head + bytes([c16 & 0xFF, (c16 >> 8) & 0xFF]) + inner
    frame += struct.pack("<I", crc32(inner))
    return frame


def verify_frame(frame: bytes) -> bool:
    """Validate a complete Format-1 (whoop5) frame: SOF, length, CRC16 header, CRC32 body."""
    if len(frame) < 13 or frame[0] != SOF:
        return False
    decl = frame[2] | (frame[3] << 8)
    total = decl + 8
    if total != len(frame):
        return False
    got16 = frame[6] | (frame[7] << 8)
    if crc16_modbus(frame[0:6]) != got16:
        return False
    payload_end = total - 4
    got32 = struct.unpack("<I", frame[payload_end:total])[0]
    return crc32(frame[8:payload_end]) == got32


class Response:
    __slots__ = ("type", "resp_seq", "cmd", "origin_seq", "result", "body", "raw")

    def __init__(self, frame: bytes):
        self.raw = frame
        self.type = frame[8]
        self.resp_seq = frame[9]
        self.cmd = frame[10]
        self.origin_seq = frame[11]
        self.result = frame[12]
        decl = frame[2] | (frame[3] << 8)
        self.body = frame[13:decl + 4]  # up to the CRC32 trailer

    @property
    def result_name(self) -> str:
        return RESULT_NAMES.get(self.result, f"UNKNOWN({self.result})")

    def __repr__(self) -> str:
        return (f"<Response cmd={self.cmd} origin_seq={self.origin_seq} "
                f"{self.result_name} body={self.body.hex()}>")


class Reassembler:
    """Accumulate BLE notification fragments into complete whoop5 frames.
    Mirrors the Reassembler(family: .whoop5) in Framing.swift."""
    MAX_FRAME = 8192
    MIN_FRAME = 13

    def __init__(self):
        self._buf = bytearray()

    def feed(self, fragment: bytes) -> list[bytes]:
        self._buf += fragment
        out: list[bytes] = []
        while True:
            sof = self._buf.find(SOF)
            if sof < 0:
                self._buf.clear()
                break
            if sof > 0:
                del self._buf[:sof]
            if len(self._buf) < 8:
                break
            decl = self._buf[2] | (self._buf[3] << 8)
            total = decl + 8
            if total < self.MIN_FRAME or total > self.MAX_FRAME:
                del self._buf[0]
                continue
            got16 = self._buf[6] | (self._buf[7] << 8)
            if crc16_modbus(bytes(self._buf[0:6])) != got16:
                del self._buf[0]  # false SOF, resync
                continue
            if len(self._buf) < total:
                break
            out.append(bytes(self._buf[:total]))
            del self._buf[:total]
        return out


# The static client hello the MG accepts: this is GET_HELLO (cmd 145) revision 1.
CLIENT_HELLO = bytes.fromhex("aa0108000001e67123019101363e5c8d")


def _selftest():
    expected = bytes.fromhex("aa0108000001e67123019101363e5c8d")
    built = build_command_frame(cmd=145, seq=1, body=b"\x01")
    assert built == expected, f"\n built={built.hex()}\n want ={expected.hex()}"
    assert verify_frame(built), "verify_frame rejected a frame we built"
    # round-trip through the reassembler, split across fragments
    r = Reassembler()
    frames = r.feed(built[:5]) + r.feed(built[5:])
    assert frames == [built], frames
    # crc primitives against the known-good hello
    assert crc16_modbus(expected[0:6]) == (expected[6] | expected[7] << 8)
    assert crc32(expected[8:12]) == struct.unpack("<I", expected[12:16])[0]
    print("framing self-test OK — built GET_HELLO rev1 ==", built.hex())
    print("                      == documented client hello", CLIENT_HELLO.hex())
    assert built == CLIENT_HELLO


if __name__ == "__main__":
    _selftest()
