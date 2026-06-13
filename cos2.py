import glob, json, os
bases = sorted(set(glob.glob("/home/*/.claude/projects")
                   + ["/root/.claude/projects", os.path.expanduser("~/.claude/projects")]))
print("bases:", bases)
for base in bases:
    for d in sorted(glob.glob(base + "/*")):
        name = os.path.basename(d); files = glob.glob(d + "/*.jsonl")
        nlines = msgs = tot = 0
        for f in files:
            for line in open(f, encoding="utf-8", errors="replace"):
                nlines += 1
                try: o = json.loads(line)
                except: continue
                u = (o.get("message") or {}).get("usage")
                if isinstance(u, dict):
                    msgs += 1
                    tot += sum(u.get(k,0) or 0 for k in
                        ("input_tokens","cache_creation_input_tokens",
                         "cache_read_input_tokens","output_tokens"))
        flag = "   <<<< CHAMADOS" if "chamado" in name.lower() else ""
        print(f"{name} | arq={len(files)} linhas={nlines} msgs_uso={msgs} tokens={tot:,}{flag}")