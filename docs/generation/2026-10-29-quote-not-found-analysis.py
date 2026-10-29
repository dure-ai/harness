import json, re, glob, collections, os
from pathlib import Path
from harness.generate.config import load_gen_config, cache_root
from harness.generate import fetch, pipeline
from harness.generate.candidates import extract_candidates, check_quotes, _fold
from harness.generate.traces import message_text, compress
from harness.generate.llm import CachedClient, _cache_settings

cfg = load_gen_config(Path("config/gen-v0.1.yaml"))
class Miss:
    name="claude-code"; model=cfg.model; last_model=None
    def complete_json(self,*a,**k): raise RuntimeError("cache miss")
client = CachedClient(Miss(), cache_root()/"llm", cfg.prompt_version, _cache_settings(cfg))

ids=set()
for f in glob.glob(os.path.expanduser("~/.cache/t5-harness/runs/*/rejected.jsonl")):
    for l in open(f):
        d=json.loads(l)
        if d["code"]=="quote_not_found": ids.add(d["trace_id"])
traces={t.trajectory_id:t for t in pipeline.read_all(fetch.local_shards(cache_root()/"traces")) if t.trajectory_id in ids}
print("traces with quote_not_found:", len(ids), "loaded:", len(traces))

def norm(s): return re.sub(r"\s+"," ",re.sub(r"[`'\"“”‘’]","",s)).strip().casefold()
cat=collections.Counter(); ex=collections.defaultdict(list); total=0
for tid,t in traces.items():
    try: cands=extract_candidates(client,t,cfg)
    except RuntimeError: print("MISS",tid); continue
    ok,rej=check_quotes(t,cands,cfg.min_quote_chars)
    comp=compress(t)
    for r in rej:
        if r.code!="quote_not_found": continue
        total+=1
        i=int(re.search(r"candidate (\d+)",r.message).group(1)); c=cands[i]
        msg=message_text(t,c.message_index); q=_fold(c.quote)
        if q.casefold() in _fold(msg).casefold(): k="A casefold only"
        elif norm(q) in norm(msg): k="B quotes/backticks stripped"
        elif any(q in _fold(message_text(t,m.index)) for m in t.messages if m.index!=c.message_index): k="C in another message (wrong index)"
        elif any(norm(q) in norm(message_text(t,m.index)) for m in t.messages if m.index!=c.message_index): k="C2 another message, normalized"
        elif q in _fold(comp): k="D in compressed text but not message"
        else:
            words=q.split(); frac=sum(w in msg for w in words)/max(len(words),1)
            k="E paraphrase (>=70% words present)" if frac>=0.7 else "F not in message"
        cat[k]+=1
        if len(ex[k])<3: ex[k].append((tid[:8],c.message_index,c.quote[:110]))
print("quote_not_found unique:",total)
for k,v in sorted(cat.items()): print(f"{v:3d}  {k}")
for k,v in sorted(ex.items()):
    print("--",k)
    for e in v: print("   ",e)

print("\n===== deep dive on E/F: longest common substring vs quote, and where")
import difflib
rows=[]
for tid,t in traces.items():
    cands=extract_candidates(client,t,cfg); ok,rej=check_quotes(t,cands,cfg.min_quote_chars)
    comp=_fold(compress(t))
    for r in rej:
        if r.code!="quote_not_found": continue
        i=int(re.search(r"candidate (\d+)",r.message).group(1)); c=cands[i]
        q=_fold(c.quote); msg=_fold(message_text(t,c.message_index))
        if q.casefold() in msg.casefold() or norm(q) in norm(msg): continue
        sm=difflib.SequenceMatcher(None,q,msg,autojunk=False); m=sm.find_longest_match(0,len(q),0,len(msg))
        ratio=m.size/len(q)
        in_comp = q in comp
        # where is the longest block in the quote: prefix? suffix? middle?
        pos="prefix" if m.a==0 else ("suffix" if m.a+m.size==len(q) else "middle")
        rows.append((ratio,pos,in_comp,tid[:8],c.message_index,q[:90],msg[max(m.b-25,0):m.b+m.size+25][:140]))
rows.sort(reverse=True)
b=collections.Counter()
for r in rows:
    b[("lcs>=0.8" if r[0]>=0.8 else "lcs 0.5-0.8" if r[0]>=0.5 else "lcs<0.5", "seen-in-compressed" if r[2] else "not-in-compressed")]+=1
for k,v in sorted(b.items()): print(f"{v:3d} {k}")
print("\n-- samples (lcs ratio, longest block position, quote | message around match)")
for r in rows[:6]+rows[len(rows)//2:len(rows)//2+4]+rows[-5:]:
    print(f"{r[0]:.2f} {r[1]:6s} comp={r[2]} {r[3]} #{r[4]}\n     Q: {r[5]!r}\n     M: {r[6]!r}")

print("\n===== recount after JSON-unescaping message text")
def unesc(s):
    # decode JSON string escapes that survive in tool-call strings (\\n, \\", \\u003e ...)
    try: return json.loads('"' + s.replace('"','\\"').replace('\\\\"','\\"') + '"')
    except Exception: return s
def unesc2(s):
    return (s.replace('\\u003e','>').replace('\\u003c','<').replace('\\u0027',"'").replace('\\u0026','&')
             .replace('\\"','"').replace('\\n','\n').replace('\\t','\t').replace('\\\\','\\'))
c2=collections.Counter(); still=[]
for tid,t in traces.items():
    cands=extract_candidates(client,t,cfg); ok,rej=check_quotes(t,cands,cfg.min_quote_chars)
    for r in rej:
        if r.code!="quote_not_found": continue
        i=int(re.search(r"candidate (\d+)",r.message).group(1)); c=cands[i]
        q=_fold(c.quote); raw=message_text(t,c.message_index); m2=_fold(unesc2(raw))
        if q in m2: c2["1 exact after unescape"]+=1
        elif norm(q) in norm(m2): c2["2 unescape + casefold/quote-strip"]+=1
        elif any(q in _fold(unesc2(message_text(t,m.index))) for m in t.messages): c2["3 unescape, other message"]+=1
        else: c2["4 still not found"]+=1; still.append((tid[:8],c.message_index,q[:100]))
for k,v in sorted(c2.items()): print(f"{v:3d} {k}")
print("-- still not found samples"); [print("   ",s) for s in still[:8]]
