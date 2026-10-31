import json
import sys
import time
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    if message['method'] == 'tools/call':
        time.sleep(3)
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {}}), flush=True)
