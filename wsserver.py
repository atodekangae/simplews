#!/usr/bin/env python3
from curio import run, tcp_server, spawn, sleep, Event
import hashlib
import base64
import itertools
import os
import json

MAGIC = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

def mask_bytes(stream, key):
    return bytes(s^k for s, k in zip(stream, key))

def gen_frame(opcode: int, payload: bytes, mask=None):
    if mask is not None:
        assert len(mask) == 4
    frame = [bytes([0x80|opcode])]
    second_byte = 0 if mask is None else 0x80
    size = len(payload)
    if size <= 125:
        second_byte |= size
        frame.append(bytes([second_byte]))
    elif size < 2**16:
        second_byte |= 126
        frame.append(bytes([second_byte, size>>8, size&0xFF]))
    else:
        second_byte |= 127
        frame.append(bytes([
            second_byte,
            size>>24,
            (size>>16)&0xFF,
            (size>>8)&0xFF,
            size&0xFF
        ]))
    if mask is None:
        frame.append(payload)
    else:
        frame.append(mask)
        frame.append(mask_bytes(payload, itertools.cycle(mask)))
    return b''.join(frame)

async def read_frame(stream):
    is_fin = False
    chunks = []
    while not is_fin:
        first_byte = (await stream.read_exactly(1))[0]
        is_fin = (False, True)[first_byte>>7]
        opcode = first_byte&0x7F
        second_byte = (await stream.read_exactly(1))[0]
        is_masked = (False, True)[second_byte>>7]
        size = second_byte&0x7F
        if size == 126:
            s = await stream.read_exactly(2)
            size = (s[0]<<8)|s[1]
        elif size == 127:
            s = await stream.read_exactly(4)
            size = (s[0]<<24)|(s[1]<<16)|(s[2]<<8)|s[3]
        if is_masked:
            mask = await stream.read_exactly(4)
            masked = await stream.read_exactly(size)
            chunks.append(mask_bytes(masked, itertools.cycle(mask)))
        else:
            chunk = await stream.read_exactly(size)
            chunks.append(chunk)
    return (opcode, b''.join(chunks))

async def read_headers(stream):
    reqline = await stream.readline()
    headers = []
    while True:
        line = await stream.readline()
        assert line.endswith(b'\r\n')
        line = line[:-2]
        if line == b'': break
        key, value = line.split(b': ')
        headers.append((key, value))
    return (reqline, headers)

def new_ws_server(client, addr):
    s = client.as_stream()
    async def ws_send(text: str):
        frame = gen_frame(1, text.encode('utf-8'))
        await s.write(frame)
    return _ws_server(s), ws_send

async def _ws_server(s):
    reqline, headers = await read_headers(s)
    headers = { k.lower(): v for k, v in headers }
    assert reqline.count(b' ') == 2
    method, uri, version = reqline.split(b' ')
    assert method == b'GET'
    assert headers[b'upgrade'] == b'websocket'
    assert b'Upgrade' in headers[b'connection']
    client_key = headers[b'sec-websocket-key']
    handshake = (
        b'HTTP/1.1 101 Switching Protocols\r\n'
        b'Upgrade: websocket\r\n'
        b'Connection: %s\r\n'
        b'Sec-WebSocket-Accept: %s\r\n\r\n'
    ) % (headers[b'connection'], base64.b64encode(hashlib.sha1(client_key+MAGIC).digest()),)
    await s.write(handshake)
    while True:
        opcode, payload = await read_frame(s)
        if opcode == 1:
            text = payload.decode('utf-8', 'ignore')
            yield text
        elif opcode == 9: # PING
            frame = gen_frame(0xa, payload)
            await s.write(frame)
        else:
            print('unknown opcode:', opcode)
            print('payload:', payload)

async def ws_server(bind, port, handler):
    async def _ws_server_handler(client, addr):
        recv, send = new_ws_server(client, addr)
        await handler(client, addr, recv, send)
    await tcp_server(bind, port, _ws_server_handler)

async def test_server(client, addr, recv, send):
    print('accepted')
    async for data in recv:
        print('received: {}'.format(data))
        await send(data.upper())

if __name__ == '__main__':
    try:
        run(ws_server, '', 8000, test_server)
    except KeyboardInterrupt:
        print('interrupted')
