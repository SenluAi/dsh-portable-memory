# -*- coding: utf-8 -*-
"""回归测试：锁超时异常、损坏自恢复、写入备份、预算不限量。"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
MEM = str(HERE / "memory.py")
sys.path.insert(0, str(HERE))
import memory as M

ok = []

def check(name, cond, extra=""):
    ok.append(bool(cond))
    print(("[OK] " if cond else "[FAIL] ") + name + ((" " + extra) if extra else ""))

tmpdir = pathlib.Path(tempfile.mkdtemp())
store_path = str(tmpdir / "store" / "memory.json")

# 1) 正常写读，且生成 .bak
M.mutate(store_path, lambda s: M.add_item(s, "preferences", text="测试条目A", value="测试条目A", kind="other"))
M.mutate(store_path, lambda s: M.add_item(s, "preferences", text="测试条目B", value="测试条目B", kind="other"))
check(".bak 备份已生成", pathlib.Path(store_path + ".bak").exists())

data = json.loads(pathlib.Path(store_path).read_text(encoding="utf-8"))
check("两条都已写入", len(data["layers"]["preferences"]["items"]) == 2,
      str(len(data["layers"]["preferences"]["items"])))

# 2) 损坏 JSON -> 自动用 .bak 恢复
pathlib.Path(store_path).write_text('{"broken": ', encoding="utf-8")
store = M.load(store_path)
n = len(store["layers"]["preferences"]["items"])
check("损坏库自动恢复", n >= 1, "恢复出 %d 条" % n)
# 恢复后文件应是合法 JSON
json.loads(pathlib.Path(store_path).read_text(encoding="utf-8"))
check("恢复后文件可正常解析", True)

# 3) 锁超时抛 LockTimeout（跨进程，非 SystemExit）
holder = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
    "import sys,time;sys.path.insert(0,r'%s');import memory as M;"
    "lk=M.store_lock(r'%s',timeout=10);"
    "lk.__enter__();time.sleep(1.5)" % (str(HERE), store_path)])
time.sleep(0.4)
try:
    with M.store_lock(store_path, timeout=0.3):
        check("锁超时抛 LockTimeout", False, "竟然拿到了锁")
except M.LockTimeout as exc:
    check("锁超时抛 LockTimeout", True, str(exc)[:40])
except SystemExit:
    check("锁超时抛 LockTimeout", False, "仍是 SystemExit")
holder.wait()

# 4) CLI：损坏 + 无 bak 时应给出明确提示而非 traceback
bare = tmpdir / "bare" / "memory.json"
bare.parent.mkdir(parents=True)
bare.write_text("not json", encoding="utf-8")
r = subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", str(bare), "stats"],
                   capture_output=True)
msg = (r.stdout + r.stderr).decode("utf-8", "replace")
check("无备份时提示清晰", r.returncode != 0 and "损坏" in msg and "Traceback" not in msg, msg.strip()[-60:])

# 5) budget 0 文案为「不限量」
r = subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", store_path, "context", "--budget", "0"],
                   capture_output=True)
out = r.stdout.decode("utf-8", "replace")
check("budget=0 显示不限量", "不限量" in out, out.strip().splitlines()[-1] if out.strip() else "")

# 5.5) prune 每天最多真正执行一次
subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", store_path, "prune"], capture_output=True)
r = subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", store_path, "prune"], capture_output=True)
out = r.stdout.decode("utf-8", "replace")
rec = json.loads(pathlib.Path(store_path).read_text(encoding="utf-8"))
check("同日第二次 prune 被跳过", "每天最多清理一次" in out and bool(rec["meta"].get("lastPrune")),
      rec["meta"].get("lastPrune", ""))
r = subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", store_path, "prune", "--force"],
                   capture_output=True)
check("--force 可强制再清", "每天最多清理一次" not in r.stdout.decode("utf-8", "replace"))
r = subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", store_path, "prune", "--dry-run"],
                   capture_output=True)
check("dry-run 不受每日限制", "每天最多清理一次" not in r.stdout.decode("utf-8", "replace"))

# 7) 召回加分不该被衰减吃掉（"越召回越轻"的回归）
wstore = M.empty_store()
wi, _ = M.add_item(wstore, "preferences", text="权重回归", weight=2.0)
wi["lastSeen"] = (M.today() - __import__("datetime").timedelta(days=30)).isoformat()
for _ in range(8):
    M.touch_item(wi, "preferences")
check("召回后权重不降", M.item_weight(wi, "preferences") >= 2.0,
      "base=%.2f eff=%.2f" % (wi["weight"], M.item_weight(wi, "preferences")))

# 8) forget --purge 彻底删除，不留归档
pstore = str(tmpdir / "purge" / "memory.json")
pathlib.Path(pstore).parent.mkdir(parents=True, exist_ok=True)
M.save(pstore, M.empty_store())
M.mutate(pstore, lambda s: M.add_item(s, "rules", text="临时规则待抹除"))
subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", pstore, "forget",
                "--query", "临时规则待抹除", "--purge"], capture_output=True)
rec = json.loads(pathlib.Path(pstore).read_text(encoding="utf-8"))
gone = not any("临时规则待抹除" in json.dumps(x, ensure_ascii=False)
               for x in rec["layers"]["rules"]["items"] + rec.get("archive", []))
check("--purge 彻底删除", gone, "archive=%d" % len(rec.get("archive", [])))

# 9) 归档不限条数，只按保留期丢超期条目
s9 = M.empty_store()
fresh = M.now_stamp()
s9["archive"] = [{"layer": "rules", "item": {"text": str(i)}, "prunedAt": fresh} for i in range(5000)]
stale = (M._dt.datetime.now() - M._dt.timedelta(days=M.ARCHIVE_TTL_DAYS + 5)).strftime("%Y-%m-%d %H:%M")
s9["archive"].append({"layer": "rules", "item": {"text": "太旧"}, "prunedAt": stale})
M._push_archive(s9, [{"layer": "rules", "item": {"text": "最新"}, "prunedAt": fresh}])
texts = [e["item"]["text"] for e in s9["archive"]]
check("归档不限条数", len(s9["archive"]) == 5001 and texts[-1] == "最新", "len=%d" % len(s9["archive"]))
check("归档超期被丢", "太旧" not in texts, "ttl=%d" % M.ARCHIVE_TTL_DAYS)

# 10) 同义词扩展：换说法也能召回
s10 = M.empty_store()
M.add_item(s10, "preferences", text="文档统一放在桌面")
toks_plain = M.tokens("文件放哪了")
toks_syn = M.expand_tokens("文件放哪了", s10)
item10 = s10["layers"]["preferences"]["items"][0]
check("同义词扩展生效", M.score(item10, toks_syn) > M.score(item10, toks_plain),
      "原词 %d -> 扩展 %d 分" % (M.score(item10, toks_plain), M.score(item10, toks_syn)))
check("扩展词不含单字噪音", all(len(t) > 1 for t in toks_syn if t in "错文件"), str(toks_syn[:6]))

# 11) alias 参与打分
s11 = M.empty_store()
it11, _ = M.add_item(s11, "preferences", text="项目代码在 D 盘", alias=["仓库根目录", "repo 路径"])
t11 = M.expand_tokens("仓库根目录在哪", s11)
check("alias 能命中", M.score(it11, t11) > 0, "score=%d" % M.score(it11, t11))

# 12) 自定义同义词写入 meta 并持久化
s12 = str(tmpdir / "syn" / "memory.json")
M.save(s12, M.empty_store())
subprocess.run([sys.executable, "-X", "utf8", MEM, "--store", s12, "synonyms",
                "--add", "桌面=桌面目录,desktop"], capture_output=True)
rec = json.loads(pathlib.Path(s12).read_text(encoding="utf-8"))
check("自定义同义词落盘", "desktop" in (rec["meta"].get("synonyms", {}).get("桌面") or []),
      json.dumps(rec["meta"].get("synonyms"), ensure_ascii=False)[:60])

# 13) 删除不做同义词扩展，避免换说法误删
s13 = M.empty_store()
M.add_item(s13, "preferences", text="文档统一放在桌面")
removed, dropped = M.forget_items(s13, query="文件")
check("forget 不因同义词误删", len(s13["layers"]["preferences"]["items"]) == 1 and not removed,
      "removed=%d" % len(removed))

# 14) 归档回退提示：只给条数和 id，不注入正文；可关闭
s14 = str(tmpdir / "arc" / "memory.json")
M.save(s14, M.empty_store())
M.mutate(s14, lambda s: M.add_item(s, "preferences", text="旧偏好：周五下午不开会",
                                   value="旧偏好：周五下午不开会", item_id="arc-001"))
M.mutate(s14, lambda s: M.forget_items(s, "arc-001"))
st14 = M.load(s14)
line14 = M.archive_hint_line(st14, "不开会")
check("归档提示只给条数和 id", "1 条" in line14 and "arc-001" in line14
      and "周五下午不开会" not in line14 and "restore" in line14, line14[:70])


def _recall14(*extra):
    return subprocess.run([sys.executable, "-u", "-X", "utf8", MEM, "--store", s14,
                           "recall", "不开会", "--no-touch", *extra],
                          capture_output=True, text=True, encoding="utf-8").stdout


check("召回为空时返回归档提示", "归档中还有 1 条" in _recall14(), _recall14().strip()[:50])
check("--no-archive-hint 可关闭", "归档中还有" not in _recall14("--no-archive-hint"),
      _recall14("--no-archive-hint").strip()[:50])
js14 = json.loads(_recall14("--json"))
check("JSON 带 _archive 提示", (js14.get("_archive") or {}).get("hits") == 1,
      json.dumps(js14.get("_archive"), ensure_ascii=False)[:60])
check("归档不写入召回正文", js14.get("preferences") == [], str(js14.get("preferences")))

# 5b) restore 后流水超限：自动压缩，恢复条目仍在层内
s15 = str(tmpdir / "store" / "restore_trim.json")
M.mutate(s15, lambda s: M.empty_store())


def _fill15(s):
    for i in range(int(M.MAX_FRAGMENTS)):
        M.add_item(s, "fragments", text="旧流水 %02d" % i)


M.mutate(s15, _fill15)
M.mutate(s15, lambda s: s["archive"].append({
    "layer": "fragments", "prunedAt": M.now_stamp(),
    "item": {"id": "arc-frag-1", "text": "被归档的流水条目", "at": M.now_stamp()},
}))
st15 = M.load(s15)
before15 = len(st15["layers"]["fragments"]["items"])
restored15, skipped15, trimmed15 = M.restore_items(st15, item_id="arc-frag-1")
items15 = st15["layers"]["fragments"]["items"]
texts15 = [(x.get("text") or "") for x in items15]
check("restore 可恢复流水条目", len(restored15) == 1 and before15 == int(M.MAX_FRAGMENTS),
      "before=%d restored=%d" % (before15, len(restored15)))
check("restore 后流水不超上限", len(items15) <= int(M.MAX_FRAGMENTS),
      "items=%d cap=%d" % (len(items15), int(M.MAX_FRAGMENTS)))
check("restore 触发了压缩", trimmed15 >= 1, "trimmed=%d" % trimmed15)
check("恢复的条目仍在层内", any("被归档的流水条目" == t for t in texts15))
check("压缩条进归档可追溯", any((e.get("item") or {}).get("text", "").startswith("旧流水")
                             for e in st15["archive"]),
      "archive=%d" % len(st15["archive"]))

# 6) Always use the isolated fixture, never a developer's real store.
r = subprocess.run([sys.executable, "-X", "utf8", MEM, '--store', store_path, "stats", '--json'], capture_output=True)
check("隔离记忆库正常", r.returncode == 0 and 'layers' in json.loads(r.stdout.decode('utf-8')))

print("\n结果：%d/%d 通过" % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
