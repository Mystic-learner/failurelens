from pathlib import Path

path = Path("demo/server.py")
src = path.read_text(encoding="utf-8")

old = 'self.send_header("Content-Type", "text/plain")'
new = 'self.send_header("Content-Type", "text/plain; charset=utf-8")'

assert src.count(old) == 1, f"anchor count = {src.count(old)}"
path.write_text(src.replace(old, new), encoding="utf-8")
print("patched")
