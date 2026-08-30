import io
p = 'vmw.py'
s = io.open(p, encoding='utf-8').read()

old = '''async def probe_workers(client, n=40):
    """Kitne alag worker processes jawab de rahe hain."""
    pids = set()
    for _ in range(n):
        r = await client.get("/api/health")
        pids.add(r.json().get("worker_pid"))
    return pids'''

new = '''async def probe_workers(n=40):
    """
    Kitne alag worker processes jawab de rahe hain.

    ⚠️ Har probe ke liye NAYA connection chahiye.

    Ek hi httpx client se 40 requests bhejo to sabko wahi ek keep-alive
    TCP connection milta hai — aur wo connection ek hi worker se juda
    hota hai. Nateeja: 4 workers chalte hue bhi jawab hamesha "1 worker"
    aata hai. (Ye test likhte waqt exactly yahi hua.)

    Worker distribution CONNECTION level pe hoti hai, request level pe
    nahi. Isliye naya connection = naya (shayad alag) worker.
    """
    pids = set()
    for _ in range(n):
        async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as c:
            r = await c.get("/api/health")
            pids.add(r.json().get("worker_pid"))
    return pids'''

assert old in s
s = s.replace(old, new)
s = s.replace("        pids = await probe_workers(client)", "        pids = await probe_workers()")
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)
print('ok')
