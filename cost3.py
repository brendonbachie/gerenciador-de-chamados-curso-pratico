import glob, json
d = "/home/casnav/.claude/projects/-mnt-c-Users-casnav-Desktop-chamados-cp"
tot = {"input_tokens":0,"cache_creation_input_tokens":0,
       "cache_read_input_tokens":0,"output_tokens":0}
msgs = sub = 0; models = {}; tmin = tmax = None
for f in glob.glob(d + "/*.jsonl"):
    for line in open(f, encoding="utf-8", errors="replace"):
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
print("msgs", msgs, "| subagente(sidechain)", sub, "| periodo", tmin, "->", tmax)
print("input_fresco   ", tot["input_tokens"])
print("cache_creation ", tot["cache_creation_input_tokens"])
print("cache_read     ", tot["cache_read_input_tokens"])
print("output         ", tot["output_tokens"])
print("modelos        ", models)