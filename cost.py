# salve como custo.py e rode: python custo.py   (ou python3 custo.py)
import glob, json, os
base = os.path.expanduser("~/.claude/projects")
dirs = [d for d in glob.glob(base + "/*") if any(k in os.path.basename(d).lower()
        for k in ("chamado", "gerenciador"))]
if not dirs:
    print("Não achei por nome. Listando TODAS as sessões (pegue a do chamados):")
    dirs = glob.glob(base + "/*")
for d in sorted(dirs):
    tot = {"input_tokens":0,"cache_creation_input_tokens":0,
           "cache_read_input_tokens":0,"output_tokens":0}
    msgs = sub = 0; models = {}; tmin = tmax = None
    for f in glob.glob(d + "/*.jsonl"):
        for line in open(f, encoding="utf-8"):
            try: o = json.loads(line)
            except: continue
            t = o.get("timestamp")
            if t: tmin = min(tmin or t, t); tmax = max(tmax or t, t)
            u = (o.get("message") or {}).get("usage")
            if not isinstance(u, dict): continue
            msgs += 1
            if o.get("isSidechain"): sub += 1
            for k in tot: tot[k] += u.get(k, 0) or 0
            m = (o.get("message") or {}).get("model", "?")
            models[m] = models.get(m, 0) + (u.get("output_tokens", 0) or 0)
    if not msgs: continue
    total = sum(tot.values())
    print("="*60)
    print("pasta:", os.path.basename(d))
    print(f"msgs={msgs} (subagente={sub}) | período {tmin} -> {tmax}")
    print(f"input_fresco={tot['input_tokens']:,} cache_creation={tot['cache_creation_input_tokens']:,}")
    print(f"cache_read={tot['cache_read_input_tokens']:,} output={tot['output_tokens']:,}")
    print(f"TOTAL={total:,}")
    print("modelos (por output):", models)