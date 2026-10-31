#!/usr/bin/env python3
"""MCP stdio adapter. All business rules live in the shared service."""
import json
import os
import pathlib
import sys

import service

STORE = os.environ.get('MEMORY_STORE') or str(pathlib.Path(__file__).resolve().parent / 'store/memory.json')
SERVER_INFO = {'name': 'portable-memory', 'version': service.VERSION}
PROTOCOL = '2024-11-05'
TOOLS = service.TOOLS


def call_tool(name, args):
    return service.execute(STORE, name, args)


def _call_tool(name, args):
    return call_tool(name, args)


def handle(message):
    if not isinstance(message, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    method, identity = message.get('method'), message.get('id')
    if method == 'initialize':
        return {'jsonrpc': '2.0', 'id': identity, 'result': {'protocolVersion': PROTOCOL,
                'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': SERVER_INFO}}
    if identity is None:
        return None
    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': identity, 'result': {}}
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': identity, 'result': {'tools': TOOLS}}
    if method == 'tools/call':
        try:
            params = message.get('params') or {}
            result = call_tool(params.get('name'), params.get('arguments') or {})
            payload = {'ok': True, **result}
            return {'jsonrpc': '2.0', 'id': identity, 'result': {
                'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
                'structuredContent': payload, 'isError': False}}
        except Exception as error:
            payload = service.error_envelope(error)
            return {'jsonrpc': '2.0', 'id': identity, 'result': {
                'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
                'structuredContent': payload, 'isError': True}}
    return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32601, 'message': 'Unknown method'}}


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    for line in getattr(sys.stdin, 'buffer', sys.stdin):
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
