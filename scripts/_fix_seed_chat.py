# -*- coding: utf-8 -*-
"""修正 seed_real_cases.py：骨架 chat 用例（{  # .*-chat）的 tool_called 改回 retrieve_knowledge。"""
import re
p = r"D:\study\aiprojcet\agent-evaluation-offline\scripts\seed_real_cases.py"
lines = open(p, encoding="utf-8").read().splitlines()
in_chat = False
changed = 0
out = []
for line in lines:
    m = re.search(r'(\d+): \{  # .*-(chat|score)', line)
    if m:
        in_chat = (m.group(2) == "chat")
    if in_chat and re.search(r'"tool": "knowledge_retrieval"', line):
        line = line.replace('"tool": "knowledge_retrieval"', '"tool": "retrieve_knowledge"')
        changed += 1
    out.append(line)
open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"修正 {changed} 处骨架 chat")
