"""Validate actual npm archive contents and boot its extracted MCP runtime."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

root = pathlib.Path(__file__).resolve().parent.parent
archive = pathlib.Path(sys.argv[1]).resolve()
runtime = ['memory.py', 'mcp_server.py', 'service.py', 'semantic.py', 'facts.py', 'LICENSE']
with tarfile.open(archive) as package:
    members = package.getmembers()
    public_layout = 'package/dsh-plugin/lib/index.js' in {item.name for item in members}
    prefix = 'package/dsh-plugin/' if public_layout else 'package/'
    allowed = {'package/README.md', 'package/cordis.patch.yml', 'package/lib/index.js', 'package/package.json'} | {prefix + 'runtime/' + name for name in runtime}
    if public_layout:
        allowed |= {'package/LICENSE', prefix + 'lib/index.js', prefix + 'cordis.patch.yml'}
    assert {item.name for item in members if item.isfile()} == allowed
    assert not any(item.issym() or item.islnk() for item in members)
    for name in runtime:
        assert hashlib.sha256(package.extractfile(prefix + 'runtime/' + name).read()).digest() == hashlib.sha256((root / name).read_bytes()).digest(), name
    with tempfile.TemporaryDirectory(prefix='portable-memory-package-') as directory:
        package.extractall(directory, filter='data')
        script = pathlib.Path(directory) / (prefix + 'runtime/mcp_server.py')
        message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        child = subprocess.run([sys.executable, str(script)], input=json.dumps(message).encode() + b'\n',
            env=dict(os.environ, MEMORY_STORE=str(pathlib.Path(directory) / 'memory.json')), capture_output=True, timeout=10)
        assert child.returncode == 0, child.stderr
        assert len(json.loads(child.stdout)['result']['tools']) == 10
        if public_layout:
            entry = pathlib.Path(directory) / 'package/lib/index.js'
            node = subprocess.run(['node', '--input-type=module', '-e',
                'const m = await import(process.argv[1]); if (m.name !== "portable-memory" || typeof m.apply !== "function") process.exit(1)', entry.as_uri()],
                capture_output=True, timeout=10)
            assert node.returncode == 0, node.stderr
print('PASS npm tarball: exact source hashes, no data/credentials, and standalone Python runtime boots')
