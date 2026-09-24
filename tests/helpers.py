from TKGSNavigator.core.crc import crc32_mpeg


def section(payload=b"", number=0, last=0, version=0, extension=1, current=True):
    length = len(payload) + 9
    head = bytes([0xA7, 0xB0 | (length >> 8), length & 255,
                  extension >> 8, extension & 255, 0xC0 | (version << 1) | int(current), number, last])
    body = head + payload
    return body + crc32_mpeg(body).to_bytes(4, "big")


def service_record(sid, name):
    raw = b"\x15" + name.encode("utf-8")
    descriptor = bytes([0x48, 3 + len(raw), 1, 0, len(raw)]) + raw
    return sid.to_bytes(2, "big") + b"\x00\xf0" + bytes([len(descriptor)]) + descriptor


def lcn_record(lcn, sid):
    return lcn.to_bytes(2, "big") + b"\x02\xf0\x02" + sid.to_bytes(2, "big")


def sample_sections():
    return [section(service_record(101, "Sample News HD") + service_record(102, "Sample Culture"), 0, 1),
            section(lcn_record(1, 101) + lcn_record(2, 102), 1, 1)]


LAMEDB4 = '''eDVB services /4/
transponders
01a40000:0001:0001
\ts 12380000:27500000:1:3:420:2:0
/
end
services
0065:01a40000:0001:0001:25:0
Sample News HD
p:Demo
0066:01a40000:0001:0001:1:0
Sample Culture
p:Demo
end
'''

LAMEDB5 = '''eDVB services /5/
t:01a40000:0001:0001,s:12380000:27500000:1:3:420:2:0
s:0065:01a40000:0001:0001:25:0,"Sample News HD",p:Demo
s:0066:01a40000:0001:0001:1:0,"Sample Culture",p:Demo
'''

# Both TKGS transponders: 12380 V 27500 carries SID 0x65/0x66, 12423 H 30000 carries SID 0x67.
LAMEDB_TWO_TRANSPONDERS = LAMEDB4.replace('''/
end
services''', '''/
01a40000:0002:0001
\ts 12423000:30000000:0:3:420:2:0
/
end
services''').replace('''p:Demo
end''', '''p:Demo
0067:01a40000:0002:0001:1:0
Sample Data H
p:Demo
end''')
